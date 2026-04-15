#!/usr/bin/python3
# -*- coding: UTF-8 -*-
import requests
import traceback
import threading
import time
from datetime import datetime
from typing import Any, List, Dict, Tuple, Optional

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType


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
                    f"S{media.get('season_id')}E{media.get('episode_id')}"
                )
            else:
                self.cache[ck].append(media)
                logger.info(
                    f"AWEmbyPush 缓存剧集：{media.get('item_name')} "
                    f"S{media.get('season_id')}E{media.get('episode_id')} "
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
        s = str(merged.get("season_id", "")).zfill(2)
        merged["episode_text"] = f"S{s} 第{ep_range}集（共{len(episodes)}集）"
        unsent = [ep for ep in episodes if not self._is_recently_sent(self._send_key(ep))]
        if not unsent:
            logger.info(f"AWEmbyPush 发送层拦截重复推送：{merged.get('item_name')} S{s} 第{ep_range}集（全部已发送过）")
            return
        logger.info(f"AWEmbyPush 合并发送 {len(episodes)} 集：{merged.get('item_name')} S{s} 第{ep_range}集")
        self._send(merged)
        for ep in episodes:
            self._record_sent(self._send_key(ep))


class AWEmbyPush(_PluginBase):
    plugin_name = "AWEmbyPush"
    plugin_desc = "原项目AWEmbyPush移植，监听 Emby/Jellyfin Webhook 入库事件，通过 Telegram / 企业微信 / Bark 发送精美媒体通知。支持TMDB元数据增强、剧集合并推送、消息去重。"
    plugin_icon = "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png"
    plugin_version = "2.0.0"
    plugin_author = "AWdress"
    author_url = "https://github.com/AWdress/MoviePilot-Plugins"
    plugin_config_prefix = "awembypush_"
    plugin_order = 20
    auth_level = 1

    _enabled: bool = False
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

    _episode_cache: Optional[_EpisodeCache] = None
    _message_fingerprints: Dict[str, float] = {}
    _fingerprint_lock: threading.Lock = None

    def init_plugin(self, config: dict = None):
        self._fingerprint_lock = threading.Lock()
        self._message_fingerprints = {}
        if not config:
            return
        self._enabled = config.get("enabled", False)
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
        self._dedup_window = int(config.get("dedup_window", 60))
        self._episode_cache_timeout = int(config.get("episode_cache_timeout", 30))
        self._episode_cache = _EpisodeCache(self._send_all_channels)
        self._episode_cache.CACHE_TIMEOUT = self._episode_cache_timeout

    @property
    def _effective_tg_token(self) -> str:
        return self._tg_bot_token or (getattr(settings, 'TELEGRAM_TOKEN', None) or "")

    @property
    def _effective_tg_chat_id(self) -> str:
        return self._tg_chat_id or (getattr(settings, 'TELEGRAM_CHAT_ID', None) or "")

    @property
    def _effective_tg_api_host(self) -> str:
        return self._tg_api_host or "https://api.telegram.org"

    @property
    def _effective_wx_corp_id(self) -> str:
        return self._wx_corp_id or (getattr(settings, 'WECHAT_CORPID', None) or "")

    @property
    def _effective_wx_corp_secret(self) -> str:
        return self._wx_corp_secret or (getattr(settings, 'WECHAT_APP_SECRET', None) or "")

    @property
    def _effective_wx_agent_id(self) -> str:
        return self._wx_agent_id or (getattr(settings, 'WECHAT_APP_ID', None) or "")

    @property
    def _effective_wx_proxy_url(self) -> str:
        return self._wx_proxy_url or (getattr(settings, 'WECHAT_PROXY', None) or "https://qyapi.weixin.qq.com")

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
        return []

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

    @eventmanager.register(EventType.WebhookMessage)
    def on_webhook_message(self, event: Event):
        if not self._enabled:
            return
        try:
            event_info: WebhookEventInfo = event.event_data
            if not event_info:
                return
            if event_info.event in ("system.webhooktest", "system.notificationtest"):
                self._send_test_notification(event_info)
                return
            if event_info.event not in ("library.new", "ItemAdded"):
                return
            if event_info.item_type not in ("MOV", "TV", "SHOW", "Episode", "Movie"):
                return
            if self._check_dedup(event_info):
                return
            self._dispatch(event_info)
        except Exception as e:
            logger.error(f"AWEmbyPush 处理 Webhook 事件失败：{e}\n{traceback.format_exc()}")

    def _send_test_notification(self, info: WebhookEventInfo):
        server_name = info.channel.upper() if info.channel else "MediaServer"
        media = {
            "item_name": "Webhook 连通性测试", "item_type": "MOV", "is_ep": False,
            "status_text": "测试通知", "episode_text": "",
            "overview": "这是一条来自 AWEmbyPush 的测试消息，说明 Webhook 通道已正常连通。",
            "image_url": "", "server_name": server_name, "channel": info.channel or "",
            "play_url": "", "tmdb_url": "", "tmdb_id": "",
            "season_id": None, "episode_id": None,
            "genres": "", "cast": "", "rating": "",
            "release_date": "", "poster_url": "", "backdrop_url": "", "still_url": "",
        }
        self._send_all_channels(media)
        logger.info("AWEmbyPush 已响应 Webhook 测试通知")

    def _dispatch(self, info: WebhookEventInfo):
        is_ep = info.item_type in ("TV", "SHOW", "Episode")
        status_text = "新剧速递" if is_ep else "新片速递"
        episode_text = ""
        if is_ep:
            s = info.season_id or ""
            e = info.episode_id or ""
            if s and e:
                episode_text = f"S{str(s).zfill(2)}E{str(e).zfill(2)}"
            elif s:
                episode_text = f"第 {s} 季"
        server_name = info.channel.upper() if info.channel else "MediaServer"
        play_url = self._build_play_url(info)
        tmdb_url = (
            f"https://www.themoviedb.org/{'tv' if is_ep else 'movie'}/{info.tmdb_id}?language=zh-CN"
            if info.tmdb_id else ""
        )
        tmdb_meta = self._fetch_tmdb_metadata(info.tmdb_id, is_ep, info.season_id, info.episode_id)
        overview = info.overview or tmdb_meta.get("overview_tmdb", "") or ""
        if is_ep:
            image_url = (
                tmdb_meta.get("still_url") or tmdb_meta.get("backdrop_url")
                or info.image_url or tmdb_meta.get("poster_url") or ""
            )
        else:
            image_url = (
                tmdb_meta.get("backdrop_url") or info.image_url
                or tmdb_meta.get("poster_url") or ""
            )
        media = {
            "item_name": info.item_name or "", "item_type": info.item_type or "",
            "is_ep": is_ep, "status_text": status_text, "episode_text": episode_text,
            "overview": overview, "image_url": image_url, "server_name": server_name,
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
        if self._effective_tg_token and self._effective_tg_chat_id:
            self._send_telegram(media)
        if self._effective_wx_corp_id and self._effective_wx_corp_secret and self._effective_wx_agent_id:
            self._send_wechat(media)
        if self._bark_server and self._bark_keys:
            self._send_bark(media)
        sent_channels = []
        if self._effective_tg_token and self._effective_tg_chat_id:
            sent_channels.append("Telegram")
        if self._effective_wx_corp_id and self._effective_wx_corp_secret and self._effective_wx_agent_id:
            sent_channels.append("微信")
        if self._bark_server and self._bark_keys:
            sent_channels.append("Bark")
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

    def _send_telegram(self, media: dict):
        type_text = media.get("genres") or ("剧集" if media.get("is_ep") else "电影")
        date_label = "📺 首播" if media.get("is_ep") else "🎬 上映"
        release_date = media.get("release_date", "") or "Unknown"
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
        caption += "─────────────────────\n\n"
        if self._enable_watch_link and media.get("play_url"):
            caption += f"▶️ <a href='{media['play_url']}'>立即观看</a>"
            if media.get("tmdb_url"):
                caption += f" | ℹ️ <a href='{media['tmdb_url']}'>了解更多</a>"
        elif media.get("tmdb_url"):
            caption += f"ℹ️ <a href='{media['tmdb_url']}'>了解更多</a>"
        photo = media.get("image_url", "")
        try:
            api = self._effective_tg_api_host
            token = self._effective_tg_token
            chat_id = self._effective_tg_chat_id
            if photo:
                resp = requests.post(f"{api}/bot{token}/sendPhoto", json={
                    "chat_id": chat_id, "photo": photo,
                    "caption": caption, "parse_mode": "HTML",
                }, timeout=15, proxies=self._proxies)
            else:
                resp = requests.post(f"{api}/bot{token}/sendMessage", json={
                    "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
                }, timeout=15, proxies=self._proxies)
            result = resp.json() if resp else {}
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
        jump_url = play_url if (self._enable_watch_link and play_url) else tmdb_url
        if not jump_url:
            jump_url = "https://www.themoviedb.org/"
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
                        [{"type": 1, "url": play_url, "title": "▶️ 立即观看"},
                         {"type": 1, "url": tmdb_url, "title": "ℹ️ 了解更多"}]
                        if (self._enable_watch_link and play_url and tmdb_url) else
                        [{"type": 1, "url": jump_url, "title": "ℹ️ 了解更多"}]
                    ),
                    "card_action": {"type": 1, "url": jump_url},
                }
                payload = {"touser": self._wx_user_id, "msgtype": "template_card",
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
                payload = {
                    "touser": self._wx_user_id, "msgtype": "news", "agentid": agent_id_val,
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
        for key in keys:
            payload = {
                "title": f"{media['server_name']} | {media['status_text']}\n【{media['item_name']}】",
                "body": body or "新内容已入库",
                "icon": server_icon, "url": url_target, "device_key": key,
            }
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
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VSwitch', 'props': {'model': 'enable_watch_link', 'label': '显示立即观看按钮'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VSwitch', 'props': {'model': 'enable_tmdb', 'label': 'TMDB 元数据增强'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
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
                            'type': 'info', 'variant': 'tonal',
                            'text': '监听 Emby/Jellyfin Webhook 入库事件，支持 TMDB 元数据增强（类型/演员/评分）、剧集合并推送、消息去重。需在媒体服务器配置 Webhook 回调：/api/v1/webhook?token=API_TOKEN（3001端口）。Telegram / 企业微信留空自动使用 MP 内置配置。'
                        }}]}]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextField', 'props': {
                            'model': 'dedup_window', 'label': '消息去重窗口（秒）',
                            'placeholder': '60', 'type': 'number',
                            'hint': '同一媒体在此时间内不重复处理', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextField', 'props': {
                            'model': 'episode_cache_timeout', 'label': '剧集合并等待时间（秒）',
                            'placeholder': '30', 'type': 'number',
                            'hint': '等待此时间后合并同一电视剧的多集入库通知', 'persistent-hint': True}}]},
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '── Telegram 配置（留空使用 MP 内置）──'}}]}]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'tg_bot_token', 'label': 'Bot Token', 'placeholder': '留空使用 MP 内置'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'tg_chat_id', 'label': 'Chat ID', 'placeholder': '留空使用 MP 内置'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'tg_api_host', 'label': 'API Host', 'placeholder': 'https://api.telegram.org'}}]},
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '── 企业微信配置（留空使用 MP 内置）──'}}]}]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_corp_id', 'label': 'Corp ID', 'placeholder': '留空使用 MP 内置'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_corp_secret', 'label': 'Corp Secret', 'placeholder': '留空使用 MP 内置'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_agent_id', 'label': 'Agent ID', 'placeholder': '留空使用 MP 内置'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                        {'component': 'VSelect', 'props': {
                            'model': 'wx_msg_type', 'label': '消息类型',
                            'items': [
                                {'title': '卡片 (news_notice)', 'value': 'news_notice'},
                                {'title': '图文 (news)', 'value': 'news'},
                            ]}}]},
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_user_id', 'label': '接收用户', 'placeholder': '@all'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'wx_proxy_url', 'label': '代理地址', 'placeholder': '留空使用 MP 内置'}}]},
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '── Bark 配置 ──'}}]}]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'bark_server', 'label': 'Bark 服务器', 'placeholder': 'https://api.day.app'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 8}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'bark_keys', 'label': '设备 Key（多个用逗号分隔）', 'placeholder': '留空则不启用 Bark'}}]},
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'emby_server_url', 'label': 'Emby 服务器地址（用于生成播放链接）', 'placeholder': 'https://your-emby-server.com'}}]}]},
            ]}
        ], {
            "enabled": False, "enable_watch_link": False, "watch_link_type": "server",
            "enable_tmdb": True, "dedup_window": 60, "episode_cache_timeout": 30,
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
