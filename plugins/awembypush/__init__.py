#!/usr/bin/python3
# -*- coding: UTF-8 -*-
import requests
import traceback
from datetime import datetime
from typing import Any, List, Dict, Tuple, Optional

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, MediaType

GENRE_MAP = {
    "Action": "动作", "Adventure": "冒险", "Animation": "动画", "Comedy": "喜剧",
    "Crime": "犯罪", "Documentary": "纪录片", "Drama": "剧情", "Family": "家庭",
    "Fantasy": "奇幻", "History": "历史", "Horror": "恐怖", "Music": "音乐",
    "Mystery": "悬疑", "Romance": "爱情", "Science Fiction": "科幻", "Sci-Fi & Fantasy": "科幻",
    "Thriller": "惊悚", "War": "战争", "Western": "西部", "Action & Adventure": "动作冒险",
    "Kids": "儿童", "News": "新闻", "Reality": "真人秀", "Soap": "肥皂剧",
    "Talk": "脱口秀", "War & Politics": "战争政治",
}


def _translate_genres(genre_names: list) -> str:
    return ", ".join(GENRE_MAP.get(g, g) for g in genre_names[:3])


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text[:limit] + "..." if len(text) > limit else text


class AWEmbyPush(_PluginBase):
    plugin_name = "AWEmbyPush 媒体通知"
    plugin_desc = "入库后通过 Telegram / 企业微信 / Bark 发送精美媒体通知，优先使用 MP 内置配置。"
    plugin_icon = "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png"
    plugin_version = "1.2.0"
    plugin_author = "AWdress"
    author_url = "https://github.com/AWdress/MoviePilot-Plugins"
    plugin_config_prefix = "awembypush_"
    plugin_order = 20
    auth_level = 1

    _enabled: bool = False
    # Telegram 自定义（留空则用 MP 内置）
    _tg_bot_token: str = ""
    _tg_chat_id: str = ""
    _tg_api_host: str = ""
    # 企业微信自定义（留空则用 MP 内置）
    _wx_corp_id: str = ""
    _wx_corp_secret: str = ""
    _wx_agent_id: str = ""
    _wx_user_id: str = "@all"
    _wx_proxy_url: str = ""
    _wx_msg_type: str = "news_notice"
    # Bark
    _bark_server: str = "https://api.day.app"
    _bark_keys: str = ""
    # 播放链接
    _enable_watch_link: bool = False
    _watch_link_type: str = "server"
    _emby_server_url: str = ""

    def init_plugin(self, config: dict = None):
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

    # ── 内置配置读取 ──────────────────────────────────────────

    @property
    def _effective_tg_token(self) -> str:
        return self._tg_bot_token or (settings.TELEGRAM_TOKEN or "")

    @property
    def _effective_tg_chat_id(self) -> str:
        return self._tg_chat_id or (settings.TELEGRAM_CHAT_ID or "")

    @property
    def _effective_tg_api_host(self) -> str:
        return self._tg_api_host or "https://api.telegram.org"

    @property
    def _effective_wx_corp_id(self) -> str:
        return self._wx_corp_id or (settings.WECHAT_CORPID or "")

    @property
    def _effective_wx_corp_secret(self) -> str:
        return self._wx_corp_secret or (settings.WECHAT_APP_SECRET or "")

    @property
    def _effective_wx_agent_id(self) -> str:
        return self._wx_agent_id or (settings.WECHAT_APP_ID or "")

    @property
    def _effective_wx_proxy_url(self) -> str:
        return self._wx_proxy_url or (settings.WECHAT_PROXY or "https://qyapi.weixin.qq.com")

    @property
    def _proxies(self) -> Optional[dict]:
        return settings.PROXY

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        logs: List[dict] = self.get_data("logs") or []
        if not logs:
            return [{'component': 'div', 'text': '暂无推送记录'}]
        rows = []
        for item in reversed(logs[-100:]):
            color = "success" if item.get("status") == "成功" else "error"
            rows.append({
                'component': 'tr',
                'content': [
                    {'component': 'td', 'props': {'class': 'whitespace-nowrap'}, 'text': item.get("time", "")},
                    {'component': 'td', 'text': item.get("title", "")},
                    {'component': 'td', 'text': item.get("channel", "")},
                    {'component': 'td', 'props': {'class': f'text-{color}'}, 'text': item.get("status", "")},
                    {'component': 'td', 'text': item.get("msg", "")},
                ]
            })
        return [
            {'component': 'VTable', 'props': {'hover': True}, 'content': [
                {'component': 'thead', 'content': [
                    {'component': 'tr', 'content': [
                        {'component': 'th', 'text': '时间'},
                        {'component': 'th', 'text': '媒体'},
                        {'component': 'th', 'text': '渠道'},
                        {'component': 'th', 'text': '状态'},
                        {'component': 'th', 'text': '详情'},
                    ]}
                ]},
                {'component': 'tbody', 'content': rows}
            ]}
        ]

    def stop_service(self):
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    # 启用开关
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VSwitch', 'props': {'model': 'enable_watch_link', 'label': '显示立即观看按钮'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VSelect', 'props': {
                                'model': 'watch_link_type', 'label': '播放链接类型',
                                'items': [
                                    {'title': 'Emby/Jellyfin 直链', 'value': 'server'},
                                    {'title': 'Forward App', 'value': 'forward'},
                                    {'title': 'Infuse', 'value': 'infuse'},
                                ]
                            }}
                        ]},
                    ]},
                    # 提示
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VAlert', 'props': {
                                'type': 'info', 'variant': 'tonal',
                                'text': 'Telegram / 企业微信 留空则自动使用 MP 系统内置配置，填写则覆盖内置配置。'
                            }}
                        ]}
                    ]},
                    # Telegram
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '── Telegram 配置（留空使用 MP 内置）──'}}
                        ]}
                    ]},
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'tg_bot_token', 'label': 'Bot Token', 'placeholder': '留空使用 MP 内置 Telegram Token'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'tg_chat_id', 'label': 'Chat ID', 'placeholder': '留空使用 MP 内置 Chat ID'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'tg_api_host', 'label': 'API Host', 'placeholder': '留空使用 https://api.telegram.org'}}
                        ]},
                    ]},
                    # 企业微信
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '── 企业微信配置（留空使用 MP 内置）──'}}
                        ]}
                    ]},
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'wx_corp_id', 'label': 'Corp ID', 'placeholder': '留空使用 MP 内置'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'wx_corp_secret', 'label': 'Corp Secret', 'placeholder': '留空使用 MP 内置'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'wx_agent_id', 'label': 'Agent ID', 'placeholder': '留空使用 MP 内置'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 3}, 'content': [
                            {'component': 'VSelect', 'props': {
                                'model': 'wx_msg_type', 'label': '消息类型',
                                'items': [
                                    {'title': '卡片 (news_notice)', 'value': 'news_notice'},
                                    {'title': '图文 (news)', 'value': 'news'},
                                ]
                            }}
                        ]},
                    ]},
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'wx_user_id', 'label': '接收用户', 'placeholder': '@all'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'wx_proxy_url', 'label': '代理地址', 'placeholder': '留空使用 MP 内置微信代理'}}
                        ]},
                    ]},
                    # Bark
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '── Bark 配置 ──'}}
                        ]}
                    ]},
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'bark_server', 'label': 'Bark 服务器', 'placeholder': 'https://api.day.app'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 8}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'bark_keys', 'label': '设备 Key（多个用逗号分隔）', 'placeholder': '留空则不启用 Bark'}}
                        ]},
                    ]},
                    # 服务器
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'emby_server_url', 'label': 'Emby 服务器地址（用于生成播放链接）', 'placeholder': 'https://your-emby-server.com'}}
                        ]}
                    ]},
                ]
            }
        ], {
            "enabled": False,
            "enable_watch_link": False,
            "watch_link_type": "server",
            "tg_bot_token": "",
            "tg_chat_id": "",
            "tg_api_host": "",
            "wx_corp_id": "",
            "wx_corp_secret": "",
            "wx_agent_id": "",
            "wx_user_id": "@all",
            "wx_proxy_url": "",
            "wx_msg_type": "news_notice",
            "bark_server": "https://api.day.app",
            "bark_keys": "",
            "emby_server_url": "",
        }

    # ── 事件处理 ──────────────────────────────────────────────

    def _log(self, title: str, channel: str, status: str, msg: str = ""):
        """写入插件日志，最多保留 200 条"""
        logs: List[dict] = self.get_data("logs") or []
        logs.append({
            "time": datetime.now().strftime("%m-%d %H:%M:%S"),
            "title": title,
            "channel": channel,
            "status": status,
            "msg": msg,
        })
        self.save_data("logs", logs[-200:])

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        if not self._enabled:
            return
        try:
            event_data = event.event_data
            if not event_data:
                return
            mediainfo: MediaInfo = event_data.get("mediainfo")
            meta = event_data.get("meta")
            if not mediainfo:
                return
            self._dispatch(mediainfo, meta)
        except Exception:
            logger.error(f"AWEmbyPush 处理入库事件失败：{traceback.format_exc()}")

    def _dispatch(self, mediainfo: MediaInfo, meta):
        is_episode = mediainfo.type == MediaType.TV
        season = (getattr(meta, 'begin_season', None) or getattr(meta, 'season', None) or 1)
        episode = (getattr(meta, 'begin_episode', None) or getattr(meta, 'episode', None) or 1)

        genre_names = []
        tmdb_info = getattr(mediainfo, 'tmdb_info', None) or {}
        raw_genres = tmdb_info.get('genres', [])
        if raw_genres:
            genre_names = [g.get('name', '') for g in raw_genres[:3] if g.get('name')]
        genres_text = _translate_genres(genre_names) if genre_names else ("剧集" if is_episode else "电影")

        cast_text = ""
        actors = getattr(mediainfo, 'actors', None) or []
        if not actors:
            credits = tmdb_info.get('credits', {})
            actors = credits.get('cast', []) if credits else []
        if actors:
            cast_text = ", ".join(a.get('name', '') for a in actors[:5] if a.get('name'))

        image_domain = "https://image.tmdb.org/t/p/w780"
        def _full_url(path: str) -> str:
            if not path:
                return ""
            return path if path.startswith("http") else f"{image_domain}{path}"

        backdrop = _full_url(mediainfo.backdrop_path)
        poster = _full_url(mediainfo.poster_path)
        release_date = (mediainfo.release_date or tmdb_info.get('first_air_date', '') or mediainfo.year or "")

        tmdb_id = str(mediainfo.tmdb_id) if mediainfo.tmdb_id else ""
        imdb_id = mediainfo.imdb_id or ""
        play_url = self._build_play_url(
            tmdb_id=tmdb_id, imdb_id=imdb_id,
            media_name=mediainfo.title or "",
            is_episode=is_episode,
            tv_season=season, tv_episode=episode,
        )
        tmdb_url = (getattr(mediainfo, 'detail_link', None)
                    or f"https://www.themoviedb.org/{'tv' if is_episode else 'movie'}/{tmdb_id}")

        server_name = ""
        try:
            if settings.MEDIASERVER:
                server_name = settings.MEDIASERVER.split(",")[0].strip()
        except Exception:
            pass
        server_name = server_name or "MoviePilot"

        media = {
            "media_name": mediainfo.title or "",
            "media_type": "Episode" if is_episode else "Movie",
            "media_rating": mediainfo.vote_average or 0,
            "media_rel": release_date,
            "media_intro": mediainfo.overview or "",
            "media_genres": genres_text,
            "media_cast": cast_text,
            "media_tmdburl": tmdb_url,
            "media_backdrop": backdrop,
            "media_poster": poster,
            "tv_season": season,
            "tv_episode": episode,
            "server_name": server_name,
            "play_url": play_url,
        }

        if self._effective_tg_token and self._effective_tg_chat_id:
            self._send_telegram(media)
        if self._effective_wx_corp_id and self._effective_wx_corp_secret and self._effective_wx_agent_id:
            self._send_wechat(media)
        if self._bark_server and self._bark_keys:
            self._send_bark(media)

    # ── 播放链接构建 ──────────────────────────────────────────

    def _build_play_url(self, tmdb_id, imdb_id, media_name, is_episode, tv_season, tv_episode) -> str:
        t = self._watch_link_type
        if t == "forward":
            media_type = "tv" if is_episode else "movie"
            if tmdb_id:
                return f"forward://tmdb?id={tmdb_id}&type={media_type}"
            elif imdb_id:
                return f"forward://imdb?id={imdb_id}"
            return f"forward://search?q={media_name}"
        if t == "infuse" and tmdb_id:
            if is_episode:
                return f"infuse://series/{tmdb_id}-{tv_season}-{tv_episode}"
            return f"infuse://movie/{tmdb_id}"
        base = self._emby_server_url
        if not base or not tmdb_id:
            return base or ""
        return f"{base}/web/index.html#!/item?id={tmdb_id}"

    # ── Telegram ─────────────────────────────────────────────

    def _send_telegram(self, media: dict):
        is_ep = media["media_type"] == "Episode"
        status_text = "新剧速递" if is_ep else "新片速递"
        date_label = "📺 首播" if is_ep else "🎬 上映"
        release_date = media["media_rel"] or "Unknown"

        caption = f"<b>{media['server_name']} | {status_text}</b>\n\n"
        caption += "─────────────────────\n\n"
        caption += f"<b>【{media['media_name']}】</b>\n"
        if is_ep:
            caption += f"第{media['tv_season']}季：第{media['tv_episode']}集 | 新更上线\n\n"
        else:
            caption += "\n"
        if media.get("media_cast"):
            caption += f"👥 主演：{media['media_cast']}\n"
        caption += f"📺 类型：{media['media_genres']}\n"
        caption += f"⭐ 评分：{media['media_rating']}\n"
        caption += f"{date_label}：{release_date}\n\n"
        if media.get("media_intro"):
            intro = _truncate(media["media_intro"], 150)
            caption += f"📝 内容简介：\n<blockquote>{intro}</blockquote>\n\n"
        caption += "─────────────────────\n\n"
        if self._enable_watch_link:
            caption += f"▶️ <a href='{media['play_url']}'>立即观看</a> | ℹ️ <a href='{media['media_tmdburl']}'>了解更多</a>\n"
        else:
            caption += f"ℹ️ <a href='{media['media_tmdburl']}'>了解更多</a>\n"

        photo = media.get("media_backdrop") or media.get("media_poster") or ""
        try:
            api = self._effective_tg_api_host
            token = self._effective_tg_token
            chat_id = self._effective_tg_chat_id
            if photo:
                url = f"{api}/bot{token}/sendPhoto"
                requests.post(url, json={
                    "chat_id": chat_id, "photo": photo,
                    "caption": caption, "parse_mode": "HTML",
                }, timeout=15, proxies=self._proxies)
            else:
                url = f"{api}/bot{token}/sendMessage"
                requests.post(url, json={
                    "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
                }, timeout=15, proxies=self._proxies)
            logger.info("AWEmbyPush Telegram 发送成功")
            self._log(media.get("media_name", ""), "Telegram", "成功")
        except Exception as e:
            logger.error(f"AWEmbyPush Telegram 发送失败：{e}")
            self._log(media.get("media_name", ""), "Telegram", "失败", str(e))
    # ── 企业微信 ──────────────────────────────────────────────

    def _get_wx_token(self) -> Optional[str]:
        try:
            url = f"{self._effective_wx_proxy_url}/cgi-bin/gettoken"
            res = requests.get(url, params={
                "corpid": self._effective_wx_corp_id,
                "corpsecret": self._effective_wx_corp_secret,
            }, timeout=10, proxies=self._proxies)
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
        is_ep = media["media_type"] == "Episode"
        status_text = "新剧速递" if is_ep else "新片速递"
        date_label = "📺 首播" if is_ep else "🎬 上映"
        release_date = media["media_rel"] or "Unknown"
        episode_text = f"第{media['tv_season']}季：第{media['tv_episode']}集" if is_ep else ""
        image_url = media.get("media_backdrop") or media.get("media_poster") or ""
        play_url = media["play_url"]
        tmdb_url = media["media_tmdburl"]
        agent_id = self._effective_wx_agent_id
        agent_id_val = int(agent_id) if str(agent_id).isdigit() else agent_id

        try:
            url = f"{self._effective_wx_proxy_url}/cgi-bin/message/send?access_token={token}"
            if self._wx_msg_type == "news_notice":
                card = {
                    "card_type": "news_notice",
                    "source": {
                        "icon_url": "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png",
                        "desc": f"{media['server_name']} | {status_text}",
                        "desc_color": 0,
                    },
                    "main_title": {
                        "title": f"【{media['media_name']}】",
                        "desc": episode_text or "新更上线",
                    },
                    "card_image": {"url": image_url, "aspect_ratio": 2.25},
                    "vertical_content_list": [
                        {"title": "👥 主演", "desc": media.get("media_cast") or "未知"},
                        {"title": "📺 类型", "desc": media["media_genres"]},
                        {"title": "⭐ 评分", "desc": str(media["media_rating"])},
                        {"title": date_label, "desc": release_date},
                        {"title": "📝 内容简介", "desc": _truncate(media.get("media_intro", ""), 120)},
                    ],
                    "jump_list": (
                        [
                            {"type": 1, "url": play_url, "title": "▶️ 立即观看"},
                            {"type": 1, "url": tmdb_url, "title": "ℹ️ 了解更多"},
                        ] if self._enable_watch_link else [
                            {"type": 1, "url": tmdb_url, "title": "ℹ️ 了解更多"},
                        ]
                    ),
                    "card_action": {"type": 1, "url": play_url if self._enable_watch_link else tmdb_url},
                }
                payload = {
                    "touser": self._wx_user_id,
                    "msgtype": "template_card",
                    "agentid": agent_id_val,
                    "template_card": card,
                }
            else:
                title_text = f"{media['server_name']} | {status_text} | 【{media['media_name']}】"
                if episode_text:
                    title_text += f" | {episode_text}"
                intro = _truncate(media.get("media_intro", ""), 100)
                desc = (
                    f"👥 主演：{media.get('media_cast') or '未知'}\n"
                    f"📺 类型：{media['media_genres']}\n"
                    f"⭐ 评分：{media['media_rating']}\n"
                    f"{date_label}：{release_date}\n\n"
                    f"📝 内容简介：{intro}"
                )
                if self._enable_watch_link:
                    desc += f"\n\nℹ️ 了解更多：{tmdb_url}"
                payload = {
                    "touser": self._wx_user_id,
                    "msgtype": "news",
                    "agentid": agent_id_val,
                    "news": {"articles": [{
                        "title": title_text,
                        "description": desc,
                        "url": play_url if self._enable_watch_link else tmdb_url,
                        "picurl": image_url,
                    }]},
                }
            res = requests.post(url, json=payload, timeout=15, proxies=self._proxies)
            data = res.json()
            if data.get("errcode", 0) == 0:
                logger.info("AWEmbyPush 企业微信发送成功")
                self._log(media.get("media_name", ""), "企业微信", "成功")
            else:
                logger.error(f"AWEmbyPush 企业微信发送失败：{data}")
                self._log(media.get("media_name", ""), "企业微信", "失败", str(data))
        except Exception as e:
            logger.error(f"AWEmbyPush 企业微信发送异常：{e}")
            self._log(media.get("media_name", ""), "企业微信", "失败", str(e))

    # ── Bark ─────────────────────────────────────────────────

    def _send_bark(self, media: dict):
        is_ep = media["media_type"] == "Episode"
        status_text = "新剧速递" if is_ep else "新片速递"
        date_label = "📺 首播" if is_ep else "🎬 上映"
        release_date = media["media_rel"] or "Unknown"
        intro = _truncate(media.get("media_intro", ""), 80)

        if is_ep:
            body = f"第{media['tv_season']}季：第{media['tv_episode']}集 | 新更上线"
            if media.get("media_cast"):
                body += f"\n👥 主演：{media['media_cast']}"
            body += f"\n📺 类型：{media['media_genres']}\n⭐ 评分：{media['media_rating']}\n{date_label}：{release_date}"
        else:
            body = ""
            if media.get("media_cast"):
                body += f"👥 主演：{media['media_cast']}\n"
            body += f"📺 类型：{media['media_genres']}\n⭐ 评分：{media['media_rating']}\n{date_label}：{release_date}"
        if intro:
            body += f"\n\n📝 内容简介：{intro}"

        url_target = media["play_url"] if self._enable_watch_link else media["media_tmdburl"]
        payload = {
            "title": f"{media['server_name']} | {status_text}\n【{media['media_name']}】",
            "body": body,
            "icon": "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png",
            "url": url_target,
            "device_keys": [k.strip() for k in self._bark_keys.split(",") if k.strip()],
        }
        try:
            res = requests.post(f"{self._bark_server}/push", json=payload,
                                timeout=15, proxies=self._proxies)
            if res.status_code == 200:
                logger.info("AWEmbyPush Bark 发送成功")
                self._log(media.get("media_name", ""), "Bark", "成功")
            else:
                logger.error(f"AWEmbyPush Bark 发送失败：{res.status_code} {res.text}")
                self._log(media.get("media_name", ""), "Bark", "失败", f"{res.status_code}")
        except Exception as e:
            logger.error(f"AWEmbyPush Bark 发送异常：{e}")
            self._log(media.get("media_name", ""), "Bark", "失败", str(e))
