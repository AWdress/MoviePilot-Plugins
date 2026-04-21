#!/usr/bin/python3
# -*- coding: UTF-8 -*-
import json
import re
import requests
import traceback
import threading
import time
from datetime import datetime
from typing import Any, List, Dict, Tuple, Optional

from starlette.requests import Request

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import SystemConfigKey


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text[:limit] + "..." if len(text) > limit else text


GENRE_MAP = {
    "Action": "动作", "Adventure": "冒险", "Animation": "动画",
    "Comedy": "喜剧", "Crime": "犯罪", "Documentary": "纪录",
    "Drama": "剧情", "Family": "家庭", "Fantasy": "奇幻",
    "History": "历史", "Horror": "恐怖", "Music": "音乐",
    "Mystery": "悬疑", "Romance": "爱情", "Science Fiction": "科幻",
    "TV Movie": "电视电影", "Thriller": "惊悚", "War": "战争",
    "Western": "西部", "Action & Adventure": "动作冒险",
    "Kids": "儿童", "News": "新闻", "Reality": "真人秀",
    "Sci-Fi & Fantasy": "科幻奇幻", "Soap": "肥皂剧",
    "Talk": "脱口秀", "War & Politics": "战争政治",
}


class _EpisodeCache:
    CACHE_TIMEOUT = 30
    SEND_DEDUP_WINDOW = 300

    def __init__(self, send_callback):
        self.cache: Dict[str, List[dict]] = {}
        self.timers: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()
        self._sent_records: Dict[str, float] = {}
        self._send = send_callback

    def _cache_key(self, media: dict) -> Optional[str]:
        if not media.get("is_ep"):
            return None
        tmdb_id = media.get("tmdb_id") or media.get("item_name", "")
        return f"{tmdb_id}_{media.get('season_id', '')}"

    def _send_key(self, media: dict) -> str:
        if media.get("is_ep"):
            return f"ep_{media.get('tmdb_id', '')}_{media.get('season_id', '')}_{media.get('episode_id', '')}"
        return f"mov_{media.get('tmdb_id', '')}"

    def _is_recently_sent(self, key: str) -> bool:
        now = time.time()
        expired = [k for k, v in self._sent_records.items() if now - v > self.SEND_DEDUP_WINDOW]
        for k in expired:
            del self._sent_records[k]
        return key in self._sent_records

    def _record_sent(self, key: str):
        self._sent_records[key] = time.time()

    def add(self, media: dict):
        if not media.get("is_ep"):
            sk = self._send_key(media)
            if self._is_recently_sent(sk):
                logger.info(f"AWEmbyPush 发送层拦截重复推送（电影）：{media.get('item_name')}")
                return
            self._send(media)
            self._record_sent(sk)
            return
        ck = self._cache_key(media)
        if not ck:
            self._send(media)
            return
        with self.lock:
            if ck in self.timers:
                self.timers[ck].cancel()
            if ck not in self.cache:
                self.cache[ck] = []
            existing_eps = [ep.get("episode_id") for ep in self.cache[ck]]
            if media.get("episode_id") in existing_eps:
                logger.info(
                    f"AWEmbyPush 剧集已在缓存中：{media.get('item_name')} "
                    f"{media.get('episode_text') or ''}"
                )
            else:
                self.cache[ck].append(media)
                logger.info(
                    f"AWEmbyPush 缓存剧集：{media.get('item_name')} "
                    f"{media.get('episode_text') or ''} "
                    f"(当前缓存 {len(self.cache[ck])} 集)"
                )
            timer = threading.Timer(self.CACHE_TIMEOUT, self._flush, args=[ck])
            timer.daemon = True
            timer.start()
            self.timers[ck] = timer

    def _flush(self, ck: str):
        with self.lock:
            episodes = self.cache.pop(ck, [])
            self.timers.pop(ck, None)
        if not episodes:
            return
        unique = {}
        for ep in episodes:
            ep_id = ep.get("episode_id")
            if ep_id not in unique:
                unique[ep_id] = ep
        episodes = sorted(unique.values(), key=lambda x: int(x.get("episode_id") or 0))
        if len(episodes) == 1:
            sk = self._send_key(episodes[0])
            if self._is_recently_sent(sk):
                logger.info(f"AWEmbyPush 发送层拦截重复推送：{episodes[0].get('item_name')}")
                return
            self._send(episodes[0])
            self._record_sent(sk)
            return
        ep_ids = [int(ep.get("episode_id") or 0) for ep in episodes]
        is_continuous = all(ep_ids[i] + 1 == ep_ids[i + 1] for i in range(len(ep_ids) - 1))
        if is_continuous:
            ep_range = f"{ep_ids[0]}-{ep_ids[-1]}" if ep_ids[0] != ep_ids[-1] else str(ep_ids[0])
        else:
            ep_range = ",".join(str(e) for e in ep_ids)
        merged = episodes[0].copy()
        merged["episode_merged"] = True
        merged["episode_range"] = ep_range
        merged["episode_count"] = len(episodes)
        s = merged.get("season_id", "")
        merged["episode_text"] = f"第{s}季：第{ep_range}集（共{len(episodes)}集）"
        unsent = [ep for ep in episodes if not self._is_recently_sent(self._send_key(ep))]
        if not unsent:
            logger.info(f"AWEmbyPush 发送层拦截重复推送：{merged.get('item_name')} 第{s}季：第{ep_range}集（全部已发送过）")
            return
        logger.info(f"AWEmbyPush 合并发送 {len(episodes)} 集：{merged.get('item_name')} 第{s}季：第{ep_range}集")
        self._send(merged)
        for ep in episodes:
            self._record_sent(self._send_key(ep))


class AWEmbyPush(_PluginBase):
    plugin_name = "AWEmbyPush"
    plugin_desc = "原项目AWEmbyPush移植，监听 Emby/Jellyfin Webhook 入库事件，通过 Telegram / 企业微信 / Bark 发送精美媒体通知。支持TMDB元数据增强、剧集合并推送、消息去重。"
    plugin_icon = "https://raw.githubusercontent.com/AWdress/MoviePilot-Plugins/main/plugins/awembypush/logo.png"
    plugin_version = "1.5.0"
    plugin_author = "AWdress"
    author_url = "https://github.com/AWdress/MoviePilot-Plugins"
    plugin_config_prefix = "awembypush_"
    plugin_order = 20
    auth_level = 1

    _enabled: bool = False
    _use_mp_tg: bool = False
    _mp_tg_channel: str = ""
    _use_mp_wx: bool = False
    _mp_wx_channel: str = ""
    _tg_bot_token: str = ""
    _tg_chat_id: str = ""
    _tg_api_host: str = ""
    _wx_corp_id: str = ""
    _wx_corp_secret: str = ""
    _wx_agent_id: str = ""
    _wx_user_id: str = "@all"
    _wx_proxy_url: str = ""
    _wx_msg_type: str = "news_notice"
    _bark_server: str = "https://api.day.app"
    _bark_keys: str = ""
    _enable_watch_link: bool = False
    _watch_link_type: str = "server"
    _emby_server_url: str = ""
    _enable_tmdb: bool = True
    _dedup_window: int = 60
    _episode_cache_timeout: int = 30
    _enable_custom_template: bool = False
    _tg_template: str = ""
    _wx_title_template: str = ""
    _wx_body_template: str = ""
    _bark_title_template: str = ""
    _bark_body_template: str = ""

    _episode_cache: Optional[_EpisodeCache] = None
    _message_fingerprints: Dict[str, float] = {}
    _fingerprint_lock: threading.Lock = None

    def init_plugin(self, config: dict = None):
        self._fingerprint_lock = threading.Lock()
        self._message_fingerprints = {}
        if not config:
            return
        self._enabled = config.get("enabled", False)
        self._use_mp_tg = config.get("use_mp_tg", False)
        self._mp_tg_channel = config.get("mp_tg_channel", "")
        self._use_mp_wx = config.get("use_mp_wx", False)
        self._mp_wx_channel = config.get("mp_wx_channel", "")
        self._tg_bot_token = config.get("tg_bot_token", "")
        self._tg_chat_id = config.get("tg_chat_id", "")
        self._tg_api_host = config.get("tg_api_host", "").rstrip("/")
        self._wx_corp_id = config.get("wx_corp_id", "")
        self._wx_corp_secret = config.get("wx_corp_secret", "")
        self._wx_agent_id = config.get("wx_agent_id", "")
        self._wx_user_id = config.get("wx_user_id", "@all")
        self._wx_proxy_url = config.get("wx_proxy_url", "").rstrip("/")
        self._wx_msg_type = config.get("wx_msg_type", "news_notice")
        self._bark_server = config.get("bark_server", "https://api.day.app").rstrip("/")
        self._bark_keys = config.get("bark_keys", "")
        self._enable_watch_link = config.get("enable_watch_link", False)
        self._watch_link_type = config.get("watch_link_type", "server")
        self._emby_server_url = config.get("emby_server_url", "").rstrip("/")
        self._enable_tmdb = config.get("enable_tmdb", True)
        self._dedup_window = int(config.get("dedup_window") or 60)
        self._episode_cache_timeout = int(config.get("episode_cache_timeout") or 30)
        self._enable_custom_template = config.get("enable_custom_template", False)
        self._tg_template = config.get("tg_template", "")
        self._wx_title_template = config.get("wx_title_template", "")
        self._wx_body_template = config.get("wx_body_template", "")
        self._bark_title_template = config.get("bark_title_template", "")
        self._bark_body_template = config.get("bark_body_template", "")
        self._episode_cache = _EpisodeCache(self._send_all_channels)
        self._episode_cache.CACHE_TIMEOUT = self._episode_cache_timeout

    def _get_mp_notification_config(self, channel_type: str, channel_name: str) -> dict:
        """从 MP 内置通知配置中获取指定渠道的 config"""
        try:
            notifications = self.systemconfig.get(SystemConfigKey.Notifications) or []
            for n in notifications:
                if (n.get("type") == channel_type
                        and n.get("enabled")
                        and n.get("name") == channel_name):
                    return n.get("config", {})
        except Exception as e:
            logger.warning(f"AWEmbyPush 读取 MP 通知配置失败：{e}")
        return {}

    @property
    def _effective_tg_token(self) -> str:
        if self._use_mp_tg and self._mp_tg_channel:
            cfg = self._get_mp_notification_config("telegram", self._mp_tg_channel)
            return cfg.get("TELEGRAM_TOKEN", "")
        return self._tg_bot_token

    @property
    def _effective_tg_chat_id(self) -> str:
        if self._use_mp_tg and self._mp_tg_channel:
            cfg = self._get_mp_notification_config("telegram", self._mp_tg_channel)
            return cfg.get("TELEGRAM_CHAT_ID", "")
        return self._tg_chat_id

    @property
    def _effective_tg_api_host(self) -> str:
        if self._use_mp_tg and self._mp_tg_channel:
            cfg = self._get_mp_notification_config("telegram", self._mp_tg_channel)
            return cfg.get("API_URL", "") or "https://api.telegram.org"
        return self._tg_api_host or "https://api.telegram.org"

    @property
    def _effective_wx_corp_id(self) -> str:
        if self._use_mp_wx and self._mp_wx_channel:
            cfg = self._get_mp_notification_config("wechat", self._mp_wx_channel)
            return cfg.get("WECHAT_CORPID", "")
        return self._wx_corp_id

    @property
    def _effective_wx_corp_secret(self) -> str:
        if self._use_mp_wx and self._mp_wx_channel:
            cfg = self._get_mp_notification_config("wechat", self._mp_wx_channel)
            return cfg.get("WECHAT_APP_SECRET", "")
        return self._wx_corp_secret

    @property
    def _effective_wx_agent_id(self) -> str:
        if self._use_mp_wx and self._mp_wx_channel:
            cfg = self._get_mp_notification_config("wechat", self._mp_wx_channel)
            return cfg.get("WECHAT_APP_ID", "")
        return self._wx_agent_id

    @property
    def _effective_wx_proxy_url(self) -> str:
        if self._use_mp_wx and self._mp_wx_channel:
            cfg = self._get_mp_notification_config("wechat", self._mp_wx_channel)
            return cfg.get("WECHAT_PROXY", "") or "https://qyapi.weixin.qq.com"
        return self._wx_proxy_url or "https://qyapi.weixin.qq.com"

    @property
    def _effective_wx_user_id(self) -> str:
        if self._use_mp_wx and self._mp_wx_channel:
            cfg = self._get_mp_notification_config("wechat", self._mp_wx_channel)
            admins = cfg.get("WECHAT_ADMINS", "")
            if admins:
                return admins
        return self._wx_user_id or "@all"

    @property
    def _proxies(self) -> Optional[dict]:
        return getattr(settings, 'PROXY', None)

    @property
    def _tmdb_api_key(self) -> str:
        return getattr(settings, 'TMDB_API_KEY', None) or ""

    @property
    def _tmdb_api_domain(self) -> str:
        return getattr(settings, 'TMDB_API_DOMAIN', None) or "api.themoviedb.org"

    @property
    def _tmdb_image_domain(self) -> str:
        return getattr(settings, 'TMDB_IMAGE_DOMAIN', None) or "image.tmdb.org"

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/webhook",
            "endpoint": self._api_webhook,
            "methods": ["POST"],
            "summary": "AWEmbyPush Webhook",
            "description": "接收 Emby/Jellyfin Webhook 回调（支持 application/json）"
        }, {
            "path": "/mp_channels",
            "endpoint": self._api_mp_channels,
            "methods": ["GET"],
            "summary": "获取 MP 通知渠道列表",
            "description": "返回 MP 内置的 Telegram / 企业微信通知渠道名称"
        }]

    def _api_mp_channels(self):
        """返回 MP 已配置的通知渠道列表，供前端下拉框使用"""
        tg_channels = []
        wx_channels = []
        try:
            notifications = self.systemconfig.get(SystemConfigKey.Notifications) or []
            for n in notifications:
                if not n.get("enabled"):
                    continue
                name = n.get("name", "")
                if n.get("type") == "telegram" and name:
                    tg_channels.append(name)
                elif n.get("type") == "wechat" and name:
                    wx_channels.append(name)
        except Exception as e:
            logger.warning(f"AWEmbyPush 获取 MP 通知渠道失败：{e}")
        return {"telegram": tg_channels, "wechat": wx_channels}

    def _preprocess_jellyfin(self, message: dict) -> dict:
        """将 Jellyfin Webhook 格式转换为 Emby 格式"""
        if "NotificationType" not in message:
            return message
        ntype = message.get("NotificationType", "")
        if ntype != "ItemAdded" or message.get("ItemType") not in ("Movie", "Episode"):
            if ntype == "NotificationTest":
                return {
                    "Event": "system.notificationtest",
                    "Server": {"Name": message.get("ServerName", ""), "Type": "Jellyfin"},
                }
            return {}
        result = {
            "Event": "library.new",
            "Item": {"ProviderIds": {}},
            "Server": {
                "Name": message.get("ServerName", ""),
                "Type": "Jellyfin",
                "Url": message.get("ServerUrl", ""),
            },
        }
        item = result["Item"]
        if message.get("ItemType") == "Movie":
            item["Type"] = "Movie"
            item["Name"] = message.get("Name", "")
        else:
            item["Type"] = "Episode"
            item["SeriesName"] = message.get("SeriesName", "")
            item["IndexNumber"] = message.get("EpisodeNumber")
            item["ParentIndexNumber"] = message.get("SeasonNumber")
        item["Id"] = message.get("ItemId", "")
        item["PremiereDate"] = str(message.get("Year", ""))
        if message.get("Provider_tmdb"):
            item["ProviderIds"]["Tmdb"] = message["Provider_tmdb"]
        if message.get("Provider_tvdb"):
            item["ProviderIds"]["Tvdb"] = message["Provider_tvdb"]
        return result

    def _parse_emby_json(self, message: dict) -> Optional[tuple]:
        """解析 Emby JSON 格式的 Webhook 报文，返回 (WebhookEventInfo, server_name, premiere_year)"""
        event_type = message.get("Event")
        if not event_type:
            return None
        event_info = WebhookEventInfo(event=event_type, channel="emby")
        # 提取服务器名称
        server_name = ""
        server_obj = message.get("Server")
        if server_obj and isinstance(server_obj, dict):
            server_name = server_obj.get("Name") or ""
        premiere_year = ""
        item = message.get("Item")
        if item:
            item_type_raw = item.get("Type")
            if item_type_raw in ("Episode", "Series", "Season"):
                event_info.item_type = "TV"
                series_name = item.get("SeriesName") or item.get("Name") or ""
                s = item.get("ParentIndexNumber")
                e = item.get("IndexNumber")
                event_info.item_name = series_name
                event_info.item_id = item.get("SeriesId") or item.get("Id")
                event_info.season_id = str(s) if s else None
                event_info.episode_id = str(e) if e else None
            elif item_type_raw == "Audio":
                event_info.item_type = "AUD"
                event_info.item_name = item.get("Album") or item.get("Name")
                event_info.item_id = item.get("AlbumId") or item.get("Id")
            else:
                event_info.item_type = "MOV"
                event_info.item_name = item.get("Name", "")
                event_info.item_id = item.get("Id")
            event_info.item_path = item.get("Path")
            event_info.tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
            event_info.overview = item.get("Overview") or ""
            # 提取年份用于 TMDB 搜索
            premiere = item.get("PremiereDate") or ""
            if premiere:
                try:
                    if premiere.isdigit():
                        premiere_year = premiere
                    else:
                        premiere_year = str(datetime.fromisoformat(
                            premiere.replace("Z", "+00:00")
                        ).year)
                except Exception:
                    pass
        if message.get("Session"):
            event_info.ip = message["Session"].get("RemoteEndPoint")
            event_info.device_name = message["Session"].get("DeviceName")
            event_info.client = message["Session"].get("Client")
        if message.get("User"):
            event_info.user_name = message["User"].get("Name")
        return (event_info, server_name, premiere_year)

    async def _api_webhook(self, request: Request):
        """独立 API 端点：接收 Emby/Jellyfin Webhook（application/json）"""
        try:
            body = await request.body()
            message = json.loads(body) if body else {}
        except Exception as e:
            logger.warning(f"AWEmbyPush API 解析请求体失败：{e}")
            return {"success": False, "message": str(e)}
        if not message:
            return {"success": False, "message": "空请求"}
        logger.debug(f"AWEmbyPush API 收到 webhook：{message.get('Event') or message.get('NotificationType')}")
        message = self._preprocess_jellyfin(message)
        if not message:
            return {"success": True, "message": "无法识别的事件"}
        result = self._parse_emby_json(message)
        if not result:
            return {"success": True, "message": "无法识别的事件"}
        event_info, server_name, premiere_year = result
        if not self._enabled:
            return {"success": True, "message": "插件未启用"}
        try:
            if event_info.event in ("system.webhooktest", "system.notificationtest"):
                self._send_test_notification(event_info, server_name=server_name)
            elif event_info.event in ("library.new", "ItemAdded"):
                if event_info.item_type in ("MOV", "TV", "SHOW", "Episode", "Movie"):
                    # 跳过 Series/Season 级别事件（无季集信息），只处理 Episode 级别
                    if event_info.item_type in ("TV", "SHOW", "Episode") and not event_info.season_id and not event_info.episode_id:
                        logger.debug(f"AWEmbyPush 跳过无季集信息的 TV 事件：{event_info.item_name}")
                        return {"success": True, "message": "skipped series/season level event"}
                    if not self._check_dedup(event_info):
                        self._dispatch(event_info, server_name=server_name, premiere_year=premiere_year)
        except Exception as e:
            logger.error(f"AWEmbyPush API 处理事件失败：{e}\n{traceback.format_exc()}")
            return {"success": False, "message": str(e)}
        return {"success": True}

    def stop_service(self):
        pass

    def _tmdb_request(self, path: str) -> Optional[dict]:
        if not self._tmdb_api_key:
            return None
        try:
            url = f"https://{self._tmdb_api_domain}/3{path}"
            sep = "&" if "?" in path else "?"
            url += f"{sep}api_key={self._tmdb_api_key}&language=zh-CN"
            resp = requests.get(url, timeout=10, proxies=self._proxies)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"AWEmbyPush TMDB API {path} 返回 {resp.status_code}")
        except Exception as e:
            logger.warning(f"AWEmbyPush TMDB API 请求失败：{e}")
        return None

    def _tmdb_image_url(self, path: str, size: str = "w500") -> str:
        if not path:
            return ""
        return f"https://{self._tmdb_image_domain}/t/p/{size}{path}"

    def _search_tmdb_id(self, name: str, media_type: str, year: str = "") -> str:
        """当 ProviderIds 无 Tmdb 时，按名称搜索 TMDB 获取 ID"""
        if not self._tmdb_api_key or not name:
            return ""
        search_type = "tv" if media_type in ("TV", "SHOW", "Episode") else "movie"
        path = f"/search/{search_type}?query={name}&page=1"
        if year:
            path += f"&year={year}"
        data = self._tmdb_request(path)
        if data and data.get("results"):
            first = data["results"][0]
            tmdb_id = str(first.get("id", ""))
            title = first.get("name") or first.get("title") or name
            logger.info(f"AWEmbyPush TMDB 搜索到：{title} (ID: {tmdb_id})")
            return tmdb_id
        logger.warning(f"AWEmbyPush TMDB 搜索无结果：{name} ({year})")
        return ""

    def _fetch_tmdb_metadata(self, tmdb_id: str, is_ep: bool,
                              season_id: Optional[str] = None,
                              episode_id: Optional[str] = None) -> dict:
        meta = {
            "genres": "", "cast": "", "rating": "",
            "release_date": "", "poster_url": "",
            "backdrop_url": "", "still_url": "", "overview_tmdb": "",
        }
        if not tmdb_id or not self._enable_tmdb or not self._tmdb_api_key:
            return meta
        try:
            if is_ep:
                self._fetch_tv_metadata(tmdb_id, season_id, episode_id, meta)
            else:
                self._fetch_movie_metadata(tmdb_id, meta)
        except Exception as e:
            logger.warning(f"AWEmbyPush TMDB 元数据获取失败：{e}")
        has_fields = [k for k, v in meta.items() if v]
        logger.info(f"AWEmbyPush TMDB 元数据 (ID={tmdb_id})：{', '.join(has_fields) if has_fields else '无数据'}")
        return meta

    def _fetch_movie_metadata(self, tmdb_id: str, meta: dict):
        data = self._tmdb_request(f"/movie/{tmdb_id}")
        if data:
            meta["rating"] = str(data.get("vote_average", ""))
            meta["release_date"] = data.get("release_date", "")
            meta["overview_tmdb"] = data.get("overview", "")
            genres = data.get("genres", [])
            if genres:
                meta["genres"] = ", ".join(GENRE_MAP.get(g["name"], g["name"]) for g in genres[:3])
            if data.get("poster_path"):
                meta["poster_url"] = self._tmdb_image_url(data["poster_path"])
            if data.get("backdrop_path"):
                meta["backdrop_url"] = self._tmdb_image_url(data["backdrop_path"])
        credits = self._tmdb_request(f"/movie/{tmdb_id}/credits")
        if credits and credits.get("cast"):
            meta["cast"] = ", ".join(a["name"] for a in credits["cast"][:5])

    def _fetch_tv_metadata(self, tmdb_id: str, season_id: Optional[str],
                            episode_id: Optional[str], meta: dict):
        tv = self._tmdb_request(f"/tv/{tmdb_id}")
        if tv:
            meta["rating"] = str(tv.get("vote_average", ""))
            meta["release_date"] = tv.get("first_air_date", "")
            if not meta.get("overview_tmdb"):
                meta["overview_tmdb"] = tv.get("overview", "")
            genres = tv.get("genres", [])
            if genres:
                meta["genres"] = ", ".join(GENRE_MAP.get(g["name"], g["name"]) for g in genres[:3])
            if tv.get("poster_path"):
                meta["poster_url"] = self._tmdb_image_url(tv["poster_path"])
            if tv.get("backdrop_path"):
                meta["backdrop_url"] = self._tmdb_image_url(tv["backdrop_path"])
        credits = self._tmdb_request(f"/tv/{tmdb_id}/credits")
        if credits and credits.get("cast"):
            meta["cast"] = ", ".join(a["name"] for a in credits["cast"][:5])
        # 获取季度海报（降级到剧集主海报）
        if season_id:
            try:
                s = int(season_id)
                season_data = self._tmdb_request(f"/tv/{tmdb_id}/season/{s}")
                if season_data and season_data.get("poster_path"):
                    meta["poster_url"] = self._tmdb_image_url(season_data["poster_path"])
                if season_data and not meta.get("release_date") and season_data.get("air_date"):
                    meta["release_date"] = season_data["air_date"]
            except (ValueError, TypeError):
                pass
        if season_id and episode_id:
            try:
                s, e = int(season_id), int(episode_id)
                ep_data = self._tmdb_request(f"/tv/{tmdb_id}/season/{s}/episode/{e}")
                if ep_data:
                    if ep_data.get("air_date"):
                        meta["release_date"] = ep_data["air_date"]
                    if ep_data.get("overview"):
                        meta["overview_tmdb"] = ep_data["overview"]
                    if ep_data.get("still_path"):
                        meta["still_url"] = self._tmdb_image_url(ep_data["still_path"])
            except (ValueError, TypeError):
                pass

    def _check_dedup(self, info: WebhookEventInfo) -> bool:
        is_ep = info.item_type in ("TV", "SHOW", "Episode")
        tmdb_id = info.tmdb_id or ""
        if info.item_type in ("MOV", "Movie"):
            media_id = tmdb_id or info.item_name or ""
            fingerprint = f"movie_{media_id}"
        elif is_ep:
            series = info.item_name or ""
            fingerprint = f"episode_{series}_{info.season_id or ''}_{info.episode_id or ''}"
        else:
            fingerprint = f"other_{info.item_name or ''}_{info.item_id or ''}"
        now = time.time()
        with self._fingerprint_lock:
            expired = [k for k, v in self._message_fingerprints.items() if now - v > self._dedup_window]
            for k in expired:
                del self._message_fingerprints[k]
            if fingerprint in self._message_fingerprints:
                elapsed = now - self._message_fingerprints[fingerprint]
                logger.info(f"AWEmbyPush 跳过重复消息（{elapsed:.1f}秒前已处理）：{info.item_name}")
                return True
            self._message_fingerprints[fingerprint] = now
        return False

    def _send_test_notification(self, info: WebhookEventInfo, server_name: str = ""):
        display_name = server_name or (info.channel.upper() if info.channel else "MediaServer")
        media = {
            "item_name": "Webhook 连通性测试", "item_type": "MOV", "is_ep": False,
            "status_text": "测试通知", "episode_text": "",
            "overview": "这是一条来自 AWEmbyPush 的测试消息，说明 Webhook 通道已正常连通。",
            "image_url": "", "server_name": display_name, "channel": info.channel or "",
            "play_url": "", "tmdb_url": "", "tmdb_id": "",
            "season_id": None, "episode_id": None,
            "genres": "", "cast": "", "rating": "",
            "release_date": "", "poster_url": "", "backdrop_url": "", "still_url": "",
        }
        self._send_all_channels(media)
        logger.info("AWEmbyPush 已响应 Webhook 测试通知")

    def _dispatch(self, info: WebhookEventInfo, server_name: str = "", premiere_year: str = ""):
        is_ep = info.item_type in ("TV", "SHOW", "Episode")
        status_text = "新剧速递" if is_ep else "新片速递"
        episode_text = ""
        if is_ep:
            s = str(info.season_id) if info.season_id else ""
            e = str(info.episode_id) if info.episode_id else ""
            if s and e:
                episode_text = f"第{s}季：第{e}集"
            elif s:
                episode_text = f"第{s}季"
        display_name = server_name or (info.channel.upper() if info.channel else "MediaServer")
        # 当 Emby 未提供 tmdb_id 时，按名称搜索 TMDB
        if not info.tmdb_id and self._enable_tmdb:
            logger.info(f"AWEmbyPush Emby 未提供 TMDB ID，搜索：{info.item_name}")
            info.tmdb_id = self._search_tmdb_id(info.item_name or "", info.item_type or "", premiere_year)
        elif info.tmdb_id:
            logger.info(f"AWEmbyPush 使用 Emby 提供的 TMDB ID：{info.tmdb_id}（{info.item_name}）")
        play_url = self._build_play_url(info)
        tmdb_url = (
            f"https://www.themoviedb.org/{'tv' if is_ep else 'movie'}/{info.tmdb_id}?language=zh-CN"
            if info.tmdb_id else ""
        )
        tmdb_meta = self._fetch_tmdb_metadata(info.tmdb_id, is_ep, info.season_id, info.episode_id)
        overview = info.overview or tmdb_meta.get("overview_tmdb", "") or ""
        # 图片降级：TMDB 剧照 > TMDB 背景 > Emby 图片 > TMDB 海报
        emby_image = ""
        if self._emby_server_url and info.item_id:
            emby_image = f"{self._emby_server_url}/Items/{info.item_id}/Images/Primary"
        if is_ep:
            image_url = (
                tmdb_meta.get("still_url") or tmdb_meta.get("backdrop_url")
                or tmdb_meta.get("poster_url") or emby_image or info.image_url or ""
            )
        else:
            image_url = (
                tmdb_meta.get("backdrop_url") or tmdb_meta.get("poster_url")
                or emby_image or info.image_url or ""
            )
        media = {
            "item_name": info.item_name or "", "item_type": info.item_type or "",
            "is_ep": is_ep, "status_text": status_text, "episode_text": episode_text,
            "overview": overview, "image_url": image_url, "server_name": display_name,
            "channel": info.channel or "", "play_url": play_url, "tmdb_url": tmdb_url,
            "tmdb_id": info.tmdb_id or "", "season_id": info.season_id, "episode_id": info.episode_id,
            "genres": tmdb_meta.get("genres", ""), "cast": tmdb_meta.get("cast", ""),
            "rating": tmdb_meta.get("rating", ""), "release_date": tmdb_meta.get("release_date", ""),
            "poster_url": tmdb_meta.get("poster_url", ""),
            "backdrop_url": tmdb_meta.get("backdrop_url", ""),
            "still_url": tmdb_meta.get("still_url", ""),
        }
        if self._episode_cache:
            self._episode_cache.add(media)
        else:
            self._send_all_channels(media)

    def _send_all_channels(self, media: dict):
        sent_channels = []
        if self._effective_tg_token and self._effective_tg_chat_id:
            self._send_telegram(media)
            sent_channels.append("Telegram")
        if self._effective_wx_corp_id and self._effective_wx_corp_secret and self._effective_wx_agent_id:
            self._send_wechat(media)
            sent_channels.append("微信")
        if self._bark_server and self._bark_keys:
            self._send_bark(media)
            sent_channels.append("Bark")
        if not sent_channels:
            logger.warning(
                f"AWEmbyPush 没有可用的通知渠道，请在插件配置中填写 Telegram / 企业微信 / Bark 任一配置。"
                f"（TG Token: {'有' if self._effective_tg_token else '无'}, "
                f"TG Chat ID: {'有' if self._effective_tg_chat_id else '无'}, "
                f"微信 Corp ID: {'有' if self._effective_wx_corp_id else '无'}, "
                f"Bark Keys: {'有' if self._bark_keys else '无'}）"
            )
        # 测试通知不记录到卡片
        if media.get("status_text") == "测试通知":
            return
        cards: List[dict] = self.get_data("recent_cards") or []
        cards.append({
            "time": datetime.now().strftime("%m-%d %H:%M"),
            "item_name": media["item_name"], "item_type": media["item_type"],
            "season_id": media.get("season_id"), "episode_id": media.get("episode_id"),
            "image_url": media.get("poster_url") or media.get("image_url", ""),
            "channel": media.get("channel", ""), "channels": " / ".join(sent_channels),
            "episode_text": media.get("episode_text", ""),
        })
        self.save_data("recent_cards", cards[-10:])

    def _build_play_url(self, info: WebhookEventInfo) -> str:
        t = self._watch_link_type
        tmdb_id = info.tmdb_id or ""
        is_ep = info.item_type in ("TV", "SHOW", "Episode")
        if t == "forward":
            media_type = "tv" if is_ep else "movie"
            if tmdb_id:
                return f"forward://tmdb?id={tmdb_id}&type={media_type}"
            return f"forward://search?q={info.item_name or ''}"
        if t == "infuse" and tmdb_id:
            if is_ep:
                s = info.season_id or 1
                e = info.episode_id or 1
                return f"infuse://series/{tmdb_id}-{s}-{e}"
            return f"infuse://movie/{tmdb_id}"
        base = self._emby_server_url
        if base and info.item_id:
            return f"{base}/web/index.html#!/item?id={info.item_id}"
        return base or ""

    def _template_context(self, media: dict) -> dict:
        return {
            "server_name": media.get("server_name", ""),
            "status_text": media.get("status_text", ""),
            "item_name": media.get("item_name", ""),
            "episode_text": media.get("episode_text", ""),
            "genres": media.get("genres", ""),
            "cast": media.get("cast", ""),
            "rating": media.get("rating", ""),
            "release_date": media.get("release_date", ""),
            "overview": media.get("overview", ""),
            "play_url": media.get("play_url", ""),
            "tmdb_url": media.get("tmdb_url", ""),
            "channel": media.get("channel", ""),
        }

    def _render_template(self, template: str, media: dict) -> str:
        if not template:
            return ""
        ctx = self._template_context(media)

        def _replace(match):
            key = match.group(1).strip()
            return str(ctx.get(key, ""))

        return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", _replace, template)

    def _send_telegram(self, media: dict):
        type_text = media.get("genres") or ("剧集" if media.get("is_ep") else "电影")
        date_label = "📺 首播" if media.get("is_ep") else "🎬 上映"
        release_date = media.get("release_date", "") or "Unknown"
        if self._enable_custom_template and self._tg_template:
            caption = self._render_template(self._tg_template, media)
        else:
            caption = f"<b>{media['server_name']} | {media['status_text']}</b>\n\n"
            caption += "─────────────────────\n\n"
            caption += f"<b>【{media['item_name']}】</b>\n"
            if media["episode_text"]:
                caption += f"{media['episode_text']} | 新更上线\n\n"
            else:
                caption += "\n"
            if media.get("cast"):
                caption += f"👥 主演：{media['cast']}\n"
            caption += f"📺 类型：{type_text}\n"
            if media.get("rating"):
                caption += f"⭐ 评分：{media['rating']}\n"
            caption += f"{date_label}：{release_date}\n\n"
            if media.get("overview"):
                caption += f"📝 内容简介：\n<blockquote>{_truncate(media['overview'], 150)}</blockquote>\n\n"
            caption += "─────────────────────"
        # 构建 InlineKeyboard 按钮（Telegram 仅支持 http/https URL）
        buttons = []
        play_url = media.get("play_url", "")
        tmdb_url = media.get("tmdb_url", "")
        if self._enable_watch_link and play_url:
            if play_url.startswith(("http://", "https://")):
                buttons.append({"text": "▶️ 立即观看", "url": play_url})
            else:
                caption += f"\n\n▶️ <a href=\"{play_url}\">立即观看</a>"
        if tmdb_url:
            buttons.append({"text": "ℹ️ 了解更多", "url": tmdb_url})
        reply_markup = {"inline_keyboard": [buttons]} if buttons else None
        photo = media.get("image_url", "")
        try:
            api = self._effective_tg_api_host
            token = self._effective_tg_token
            chat_id = self._effective_tg_chat_id
            payload = {"chat_id": chat_id, "parse_mode": "HTML"}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            if photo:
                payload["photo"] = photo
                payload["caption"] = caption
                resp = requests.post(f"{api}/bot{token}/sendPhoto", json=payload,
                                     timeout=15, proxies=self._proxies)
            else:
                payload["text"] = caption
                resp = requests.post(f"{api}/bot{token}/sendMessage", json=payload,
                                     timeout=15, proxies=self._proxies)
            if resp.status_code != 200:
                logger.error(f"AWEmbyPush Telegram HTTP {resp.status_code}：{resp.text[:500]}")
                return
            result = resp.json()
            if result.get("ok"):
                logger.info(f"AWEmbyPush Telegram 发送成功：{media['item_name']}")
            else:
                logger.error(f"AWEmbyPush Telegram 发送失败：{result}")
        except Exception as e:
            logger.error(f"AWEmbyPush Telegram 发送失败：{e}")

    def _get_wx_token(self) -> Optional[str]:
        try:
            res = requests.get(
                f"{self._effective_wx_proxy_url}/cgi-bin/gettoken",
                params={"corpid": self._effective_wx_corp_id, "corpsecret": self._effective_wx_corp_secret},
                timeout=10, proxies=self._proxies
            )
            data = res.json()
            if data.get("errcode", 0) == 0:
                return data["access_token"]
            logger.error(f"获取企业微信 token 失败：{data}")
        except Exception as e:
            logger.error(f"获取企业微信 token 异常：{e}")
        return None

    def _send_wechat(self, media: dict):
        token = self._get_wx_token()
        if not token:
            return
        image_url = media.get("image_url", "")
        play_url = media.get("play_url", "")
        tmdb_url = media.get("tmdb_url", "")
        # 企业微信只支持 http/https URL
        safe_play_url = play_url if play_url.startswith(("http://", "https://")) else ""
        safe_tmdb_url = tmdb_url if tmdb_url.startswith(("http://", "https://")) else ""
        jump_url = (safe_play_url if (self._enable_watch_link and safe_play_url)
                    else safe_tmdb_url or "https://www.themoviedb.org/")
        agent_id = self._effective_wx_agent_id
        agent_id_val = int(agent_id) if str(agent_id).isdigit() else agent_id
        episode_text = media.get("episode_text", "") or "新更上线"
        type_text = media.get("genres") or ("剧集" if media.get("is_ep") else "电影")
        date_label = "📺 首播" if media.get("is_ep") else "🎬 上映"
        release_date = media.get("release_date", "") or "Unknown"
        server_icon = f"https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/{(media.get('channel') or 'emby').lower()}.png"
        try:
            url = f"{self._effective_wx_proxy_url}/cgi-bin/message/send?access_token={token}"
            if self._wx_msg_type == "news_notice":
                vertical_content = []
                if media.get("cast"):
                    vertical_content.append({"title": "👥 主演", "desc": media["cast"]})
                vertical_content.append({"title": "📺 类型", "desc": type_text})
                if media.get("rating"):
                    vertical_content.append({"title": "⭐ 评分", "desc": media["rating"]})
                vertical_content.append({"title": date_label, "desc": release_date})
                if media.get("overview"):
                    vertical_content.append({"title": "📝 内容简介", "desc": _truncate(media["overview"], 120)})
                card = {
                    "card_type": "news_notice",
                    "source": {"icon_url": server_icon, "desc": f"{media['server_name']} | {media['status_text']}", "desc_color": 0},
                    "main_title": {"title": f"【{media['item_name']}】", "desc": episode_text},
                    "card_image": {"url": image_url, "aspect_ratio": 2.25},
                    "vertical_content_list": vertical_content,
                    "jump_list": (
                        [{"type": 1, "url": safe_play_url, "title": "▶️ 立即观看"},
                         {"type": 1, "url": safe_tmdb_url, "title": "ℹ️ 了解更多"}]
                        if (self._enable_watch_link and safe_play_url and safe_tmdb_url) else
                        [{"type": 1, "url": jump_url, "title": "ℹ️ 了解更多"}]
                    ),
                    "card_action": {"type": 1, "url": jump_url},
                }
                if self._enable_custom_template:
                    if self._wx_title_template:
                        card["main_title"]["title"] = self._render_template(self._wx_title_template, media)
                    if self._wx_body_template:
                        card["vertical_content_list"] = [{
                            "title": "📝 自定义内容",
                            "desc": _truncate(self._render_template(self._wx_body_template, media), 500),
                        }]
                payload = {"touser": self._effective_wx_user_id, "msgtype": "template_card",
                           "agentid": agent_id_val, "template_card": card}
            else:
                title_text = f"{media['server_name']} | {media['status_text']} | 【{media['item_name']}】"
                if media.get("episode_text"):
                    title_text += f" | {media['episode_text']}"
                desc_parts = []
                if media.get("cast"):
                    desc_parts.append(f"👥 主演：{media['cast']}")
                desc_parts.append(f"📺 类型：{type_text}")
                if media.get("rating"):
                    desc_parts.append(f"⭐ 评分：{media['rating']}")
                desc_parts.append(f"{date_label}：{release_date}")
                if media.get("overview"):
                    desc_parts.append(f"\n📝 内容简介：{_truncate(media['overview'], 100)}")
                if self._enable_custom_template:
                    if self._wx_title_template:
                        title_text = self._render_template(self._wx_title_template, media)
                    if self._wx_body_template:
                        desc_parts = [self._render_template(self._wx_body_template, media)]
                payload = {
                    "touser": self._effective_wx_user_id, "msgtype": "news", "agentid": agent_id_val,
                    "news": {"articles": [{"title": title_text, "description": "\n".join(desc_parts),
                                           "url": jump_url, "picurl": image_url}]},
                }
            res = requests.post(url, json=payload, timeout=15, proxies=self._proxies)
            data = res.json()
            if data.get("errcode", 0) == 0:
                logger.info(f"AWEmbyPush 企业微信发送成功：{media['item_name']}")
            else:
                logger.error(f"AWEmbyPush 企业微信发送失败：{data}")
        except Exception as e:
            logger.error(f"AWEmbyPush 企业微信发送异常：{e}")

    def _send_bark(self, media: dict):
        type_text = media.get("genres") or ("剧集" if media.get("is_ep") else "电影")
        date_label = "📺 首播" if media.get("is_ep") else "🎬 上映"
        release_date = media.get("release_date", "") or "Unknown"
        if self._enable_custom_template and self._bark_body_template:
            body = self._render_template(self._bark_body_template, media)
        else:
            body = ""
            if media.get("episode_text"):
                body += f"{media['episode_text']} | 新更上线\n"
            if media.get("cast"):
                body += f"👥 主演：{media['cast']}\n"
            body += f"📺 类型：{type_text}\n"
            if media.get("rating"):
                body += f"⭐ 评分：{media['rating']}\n"
            body += f"{date_label}：{release_date}"
            if media.get("overview"):
                body += f"\n\n📝 {_truncate(media['overview'], 80)}"
        url_target = (media.get("play_url") if (self._enable_watch_link and media.get("play_url"))
                      else media.get("tmdb_url", ""))
        server_icon = f"https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/{(media.get('channel') or 'emby').lower()}.png"
        keys = [k.strip() for k in self._bark_keys.split(",") if k.strip()]
        # 选取缩略图：剧集优先 still > backdrop > poster
        image_url = media.get("image_url") or media.get("poster_url") or ""
        # 分组：按剧集/电影分类
        group = "新剧速递" if media.get("is_ep") else "新片速递"
        for key in keys:
            title = f"{media['server_name']} | {media['status_text']}\n【{media['item_name']}】"
            if self._enable_custom_template and self._bark_title_template:
                title = self._render_template(self._bark_title_template, media)
            payload = {
                "title": title,
                "body": body or "新内容已入库",
                "icon": server_icon, "url": url_target, "device_key": key,
                "group": group,
            }
            if image_url:
                payload["image"] = image_url
            try:
                res = requests.post(f"{self._bark_server}/push", json=payload,
                                    timeout=15, proxies=self._proxies)
                if res.status_code == 200:
                    logger.info(f"AWEmbyPush Bark ({key[:8]}...) 发送成功：{media['item_name']}")
                else:
                    logger.error(f"AWEmbyPush Bark ({key[:8]}...) 发送失败：{res.status_code} {res.text}")
            except Exception as e:
                logger.error(f"AWEmbyPush Bark ({key[:8]}...) 发送异常：{e}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 动态获取 MP 已配置的通知渠道
        tg_items = []
        wx_items = []
        try:
            notifications = self.systemconfig.get(SystemConfigKey.Notifications) or []
            for n in notifications:
                if not n.get("enabled"):
                    continue
                name = n.get("name", "")
                if n.get("type") == "telegram" and name:
                    tg_items.append({"title": name, "value": name})
                elif n.get("type") == "wechat" and name:
                    wx_items.append({"title": name, "value": name})
        except Exception:
            pass

        # ── 构建 Telegram 区块 ──
        tg_rows = [
            {'component': 'VRow', 'props': {'class': 'mt-4'}, 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VAlert', 'props': {
                        'type': 'success', 'variant': 'tonal',
                        'text': '📬 Telegram 通知配置'}}]}]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VSwitch', 'props': {
                        'model': 'use_mp_tg', 'label': '使用 MP 内置 TG 配置', 'color': 'success',
                        'hint': '保存后刷新页面生效', 'persistent-hint': True}}]},
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VSelect', 'props': {
                        'model': 'mp_tg_channel', 'label': '选择 TG 通知渠道',
                        'items': tg_items,
                        'hint': f'检测到 {len(tg_items)} 个已启用的 Telegram 渠道' if tg_items else '未检测到已启用的 Telegram 渠道',
                        'persistent-hint': True}}]},
            ]},
        ]
        if not self._use_mp_tg:
            tg_rows.append(
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {
                            'model': 'tg_bot_token', 'label': 'Bot Token',
                            'hint': '通过 @BotFather 获取', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {
                            'model': 'tg_chat_id', 'label': 'Chat ID',
                            'hint': '目标用户或群组 ID', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {
                            'model': 'tg_api_host', 'label': 'API Host',
                            'placeholder': 'https://api.telegram.org',
                            'hint': '自建反代可修改，默认官方地址', 'persistent-hint': True}}]},
                ]}
            )

        # ── 构建企业微信区块 ──
        wx_rows = [
            {'component': 'VRow', 'props': {'class': 'mt-4'}, 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VAlert', 'props': {
                        'type': 'warning', 'variant': 'tonal',
                        'text': '💼 企业微信通知配置'}}]}]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VSwitch', 'props': {
                        'model': 'use_mp_wx', 'label': '使用 MP 内置微信配置', 'color': 'warning',
                        'hint': '保存后刷新页面生效', 'persistent-hint': True}}]},
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VSelect', 'props': {
                        'model': 'mp_wx_channel', 'label': '选择微信通知渠道',
                        'items': wx_items,
                        'hint': f'检测到 {len(wx_items)} 个已启用的企业微信渠道' if wx_items else '未检测到已启用的企业微信渠道',
                        'persistent-hint': True}}]},
            ]},
        ]
        if not self._use_mp_wx:
            wx_rows.append(
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_corp_id', 'label': 'Corp ID',
                            'hint': '企业 ID', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_corp_secret', 'label': 'Corp Secret',
                            'hint': '应用密钥', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_agent_id', 'label': 'Agent ID',
                            'hint': '应用 ID', 'persistent-hint': True}}]},
                ]}
            )
        # 消息类型 + 可选手动字段
        wx_rows.append(
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VSelect', 'props': {
                        'model': 'wx_msg_type', 'label': '消息类型',
                        'items': [
                            {'title': '卡片 (news_notice) - 不支持微信插件', 'value': 'news_notice'},
                            {'title': '图文 (news) - 支持微信插件', 'value': 'news'},
                        ],
                        'hint': '微信插件仅支持图文(news)格式', 'persistent-hint': True}}]},
            ] + ([
                {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                    {'component': 'VTextField', 'props': {'model': 'wx_user_id', 'label': '接收用户',
                        'placeholder': '@all', 'hint': '默认推送全员', 'persistent-hint': True}}]},
                {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                    {'component': 'VTextField', 'props': {'model': 'wx_proxy_url', 'label': '代理地址',
                        'placeholder': 'https://qyapi.weixin.qq.com',
                        'hint': '自建代理可修改', 'persistent-hint': True}}]},
            ] if not self._use_mp_wx else [])}
        )

        # ── 组装完整表单 ──
        form_content = [
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 6, 'md': 3}, 'content': [
                    {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件', 'color': 'primary'}}]},
                {'component': 'VCol', 'props': {'cols': 6, 'md': 3}, 'content': [
                    {'component': 'VSwitch', 'props': {'model': 'enable_tmdb', 'label': 'TMDB 增强', 'color': 'primary'}}]},
                {'component': 'VCol', 'props': {'cols': 6, 'md': 3}, 'content': [
                    {'component': 'VSwitch', 'props': {'model': 'enable_watch_link', 'label': '观看按钮', 'color': 'primary'}}]},
                {'component': 'VCol', 'props': {'cols': 6, 'md': 3}, 'content': [
                    {'component': 'VSelect', 'props': {
                        'model': 'watch_link_type', 'label': '播放链接类型',
                        'items': [
                            {'title': 'Emby/Jellyfin 直链', 'value': 'server'},
                            {'title': 'Forward App', 'value': 'forward'},
                            {'title': 'Infuse', 'value': 'infuse'},
                        ]}}]},
            ]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VAlert', 'props': {
                        'type': 'info', 'variant': 'tonal', 'style': 'font-size: 13px;',
                        'text': '📡 Webhook 地址：http(s)://MP地址:3001/api/v1/plugin/AWEmbyPush/webhook?apikey=你的API密钥\n📌 Emby/Jellyfin 中请求内容类型请选择 application/json'
                    }}]}]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VTextField', 'props': {
                        'model': 'emby_server_url', 'label': '🖥️ Emby/Jellyfin 服务器地址',
                        'placeholder': 'https://your-emby-server.com',
                        'hint': '用于生成播放链接（开启"观看按钮"时需填写）', 'persistent-hint': True}}]}]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VTextField', 'props': {
                        'model': 'dedup_window', 'label': '⏱️ 消息去重窗口（秒）',
                        'placeholder': '60', 'type': 'number',
                        'hint': '同一媒体在此时间内不重复处理', 'persistent-hint': True}}]},
                {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                    {'component': 'VTextField', 'props': {
                        'model': 'episode_cache_timeout', 'label': '⏱️ 剧集合并等待（秒）',
                        'placeholder': '30', 'type': 'number',
                        'hint': '等待此时间后合并同一电视剧的多集入库通知', 'persistent-hint': True}}]},
            ]},
            {'component': 'VRow', 'props': {'class': 'mt-2'}, 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VSwitch', 'props': {
                        'model': 'enable_custom_template',
                        'label': '🧪 启用自定义推送模板（测试功能）',
                        'color': 'warning',
                        'hint': '测试中，不建议轻易在生产环境使用',
                        'persistent-hint': True,
                    }}]}]},
        ]

        if self._enable_custom_template:
            form_content.extend([
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VAlert', 'props': {
                            'type': 'warning',
                            'variant': 'tonal',
                            'text': '⚠️ 自定义模板处于测试阶段，变量写错会导致样式异常，请谨慎使用。可用变量：{{server_name}} {{status_text}} {{item_name}} {{episode_text}} {{genres}} {{cast}} {{rating}} {{release_date}} {{overview}} {{play_url}} {{tmdb_url}}'
                        }}]}]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VTextarea', 'props': {
                            'model': 'tg_template',
                            'label': 'Telegram 模板（HTML）',
                            'rows': 4,
                            'placeholder': '<b>{{server_name}} | {{status_text}}</b>\n<b>【{{item_name}}】</b>\n{{episode_text}}\n📺 {{genres}}\n⭐ {{rating}}',
                            'hint': '启用后覆盖 Telegram 默认正文模板',
                            'persistent-hint': True,
                        }}]}]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextarea', 'props': {
                            'model': 'wx_title_template',
                            'label': '企业微信标题模板',
                            'rows': 3,
                            'placeholder': '{{server_name}} | {{status_text}} | 【{{item_name}}】',
                            'persistent-hint': True,
                        }}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextarea', 'props': {
                            'model': 'wx_body_template',
                            'label': '企业微信正文模板',
                            'rows': 3,
                            'placeholder': '{{episode_text}}\n📺 {{genres}}\n👥 {{cast}}\n⭐ {{rating}}\n{{overview}}',
                            'persistent-hint': True,
                        }}]},
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextarea', 'props': {
                            'model': 'bark_title_template',
                            'label': 'Bark 标题模板',
                            'rows': 3,
                            'placeholder': '{{server_name}} | {{status_text}}\n【{{item_name}}】',
                            'persistent-hint': True,
                        }}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextarea', 'props': {
                            'model': 'bark_body_template',
                            'label': 'Bark 正文模板',
                            'rows': 3,
                            'placeholder': '{{episode_text}}\n📺 {{genres}}\n⭐ {{rating}}\n{{overview}}',
                            'persistent-hint': True,
                        }}]},
                ]},
            ])

        form_content.extend(tg_rows)
        form_content.extend(wx_rows)

        # ── Bark 配置 ──
        form_content.extend([
            {'component': 'VRow', 'props': {'class': 'mt-4'}, 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VAlert', 'props': {
                        'type': 'error', 'variant': 'tonal',
                        'text': '🔔 Bark 通知配置（iOS）'}}]}]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                    {'component': 'VTextField', 'props': {'model': 'bark_server', 'label': 'Bark 服务器',
                        'placeholder': 'https://api.day.app',
                        'hint': '自建服务器可修改', 'persistent-hint': True}}]},
                {'component': 'VCol', 'props': {'cols': 12, 'md': 8}, 'content': [
                    {'component': 'VTextField', 'props': {'model': 'bark_keys', 'label': '设备 Key',
                        'placeholder': '多个 Key 用英文逗号分隔',
                        'hint': '留空则不启用 Bark 推送', 'persistent-hint': True}}]},
            ]},
        ])

        return [
            {'component': 'VForm', 'content': form_content}
        ], {
            "enabled": False, "enable_watch_link": False, "watch_link_type": "server",
            "enable_tmdb": True, "dedup_window": 60, "episode_cache_timeout": 30,
            "enable_custom_template": False,
            "use_mp_tg": True, "mp_tg_channel": "", "use_mp_wx": True, "mp_wx_channel": "",
            "tg_template": "", "wx_title_template": "", "wx_body_template": "",
            "bark_title_template": "", "bark_body_template": "",
            "tg_bot_token": "", "tg_chat_id": "", "tg_api_host": "",
            "wx_corp_id": "", "wx_corp_secret": "", "wx_agent_id": "",
            "wx_user_id": "@all", "wx_proxy_url": "", "wx_msg_type": "news_notice",
            "bark_server": "https://api.day.app", "bark_keys": "", "emby_server_url": "",
        }

    def get_page(self) -> List[dict]:
        cards: List[dict] = self.get_data("recent_cards") or []
        if not cards:
            return [{'component': 'div', 'props': {'class': 'text-center'}, 'text': '暂无推送记录'}]
        contents = []
        for card in reversed(cards):
            is_ep = card.get("item_type") in ["TV", "SHOW"]
            subtitle_parts = []
            if card.get("episode_text"):
                subtitle_parts.append(card["episode_text"])
            elif is_ep and card.get("season_id"):
                ep_str = f"S{str(card['season_id']).zfill(2)}"
                if card.get("episode_id"):
                    ep_str += f"E{str(card['episode_id']).zfill(2)}"
                subtitle_parts.append(ep_str)
            if card.get("channels"):
                subtitle_parts.append(f"📡 {card['channels']}")
            subtitle = "  |  ".join(subtitle_parts)
            contents.append({
                'component': 'VCard', 'props': {'variant': 'tonal'},
                'content': [{'component': 'div',
                    'props': {'class': 'd-flex justify-space-start flex-nowrap flex-row'},
                    'content': [
                        {'component': 'div', 'content': [{'component': 'VImg', 'props': {
                            'src': card.get("image_url", ""), 'height': 120, 'width': 80,
                            'aspect-ratio': '2/3', 'class': 'object-cover shadow ring-gray-500', 'cover': True}}]},
                        {'component': 'div', 'props': {'class': 'flex-1 min-w-0'}, 'content': [
                            {'component': 'VCardTitle',
                             'props': {'class': 'ps-2 pe-2 break-words whitespace-break-spaces'},
                             'text': card.get("item_name", "")},
                            {'component': 'VCardText', 'props': {'class': 'pa-0 px-2 text-caption'},
                             'text': subtitle},
                            {'component': 'VCardText', 'props': {'class': 'pa-0 px-2 text-caption'},
                             'text': f"🕐 {card.get('time', '')}  {card.get('channel', '').upper()}"},
                        ]}]}]})
        return [{'component': 'div', 'props': {'class': 'grid gap-3 grid-info-card'}, 'content': contents}]
