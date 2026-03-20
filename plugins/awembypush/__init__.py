#!/usr/bin/python3
# -*- coding: UTF-8 -*-
import requests
import traceback
from datetime import datetime
from typing import Any, List, Dict, Tuple, Optional

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType

GENRE_MAP = {
    "Action": "动作", "Adventure": "冒险", "Animation": "动画", "Comedy": "喜剧",
    "Crime": "犯罪", "Documentary": "纪录片", "Drama": "剧情", "Family": "家庭",
    "Fantasy": "奇幻", "History": "历史", "Horror": "恐怖", "Music": "音乐",
    "Mystery": "悬疑", "Romance": "爱情", "Science Fiction": "科幻", "Sci-Fi & Fantasy": "科幻",
    "Thriller": "惊悚", "War": "战争", "Western": "西部", "Action & Adventure": "动作冒险",
    "Kids": "儿童", "News": "新闻", "Reality": "真人秀", "Soap": "肥皂剧",
    "Talk": "脱口秀", "War & Politics": "战争政治",
}


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text[:limit] + "..." if len(text) > limit else text


class AWEmbyPush(_PluginBase):
    plugin_name = "AWEmbyPush"
    plugin_desc = "原项目AWEmbyPush移植，监听 Emby/Jellyfin Webhook 入库事件，通过 Telegram / 企业微信 / Bark 发送精美媒体通知。"
    plugin_icon = "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png"
    plugin_version = "1.0.0"
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

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {'component': 'VForm', 'content': [
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
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VAlert', 'props': {
                            'type': 'info', 'variant': 'tonal',
                            'text': '监听 Emby/Jellyfin Webhook 入库事件。需在媒体服务器配置 Webhook 回调：/api/v1/webhook?token=API_TOKEN（3001端口）。Telegram / 企业微信留空自动使用 MP 内置配置。'
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
                        {'component': 'VTextField', 'props': {'model': 'tg_bot_token', 'label': 'Bot Token', 'placeholder': '留空使用 MP 内置'}}
                    ]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'tg_chat_id', 'label': 'Chat ID', 'placeholder': '留空使用 MP 内置'}}
                    ]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'tg_api_host', 'label': 'API Host', 'placeholder': 'https://api.telegram.org'}}
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
                        {'component': 'VTextField', 'props': {'model': 'wx_proxy_url', 'label': '代理地址', 'placeholder': '留空使用 MP 内置'}}
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
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                        {'component': 'VTextField', 'props': {'model': 'emby_server_url', 'label': 'Emby 服务器地址（用于生成播放链接）', 'placeholder': 'https://your-emby-server.com'}}
                    ]}
                ]},
            ]}
        ], {
            "enabled": False, "enable_watch_link": False, "watch_link_type": "server",
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
            if is_ep and card.get("season_id"):
                ep_str = f"S{card['season_id']:02d}"
                if card.get("episode_id"):
                    ep_str += f"E{card['episode_id']:02d}"
                subtitle_parts.append(ep_str)
            if card.get("channels"):
                subtitle_parts.append(f"📡 {card['channels']}")
            subtitle = "  |  ".join(subtitle_parts)
            contents.append({
                'component': 'VCard',
                'props': {'variant': 'tonal'},
                'content': [{
                    'component': 'div',
                    'props': {'class': 'd-flex justify-space-start flex-nowrap flex-row'},
                    'content': [
                        {'component': 'div', 'content': [{
                            'component': 'VImg',
                            'props': {
                                'src': card.get("image_url", ""),
                                'height': 120, 'width': 80,
                                'aspect-ratio': '2/3',
                                'class': 'object-cover shadow ring-gray-500',
                                'cover': True,
                            }
                        }]},
                        {'component': 'div', 'props': {'class': 'flex-1 min-w-0'}, 'content': [
                            {'component': 'VCardTitle',
                             'props': {'class': 'ps-2 pe-2 break-words whitespace-break-spaces'},
                             'text': card.get("item_name", "")},
                            {'component': 'VCardText',
                             'props': {'class': 'pa-0 px-2 text-caption'},
                             'text': subtitle},
                            {'component': 'VCardText',
                             'props': {'class': 'pa-0 px-2 text-caption'},
                             'text': f"🕐 {card.get('time', '')}  {card.get('channel', '').upper()}"},
                        ]},
                    ]
                }]
            })
        return [{'component': 'div', 'props': {'class': 'grid gap-3 grid-info-card'}, 'content': contents}]

    # ── 事件处理 ──────────────────────────────────────────────

    @eventmanager.register(EventType.WebhookMessage)
    def on_webhook_message(self, event: Event):
        if not self._enabled:
            return
        try:
            event_info: WebhookEventInfo = event.event_data
            if not event_info:
                return
            # 只处理入库事件
            if event_info.event not in ("library.new", "LibraryChanged"):
                return
            # 只处理影片和剧集
            if event_info.item_type not in ("MOV", "TV", "SHOW", "Episode", "Movie"):
                return
            self._dispatch(event_info)
        except Exception as e:
            logger.error(f"AWEmbyPush 处理 Webhook 事件失败：{e}\n{traceback.format_exc()}")

    def _dispatch(self, info: WebhookEventInfo):
        is_ep = info.item_type in ("TV", "SHOW", "Episode")
        status_text = "新剧速递" if is_ep else "新片速递"

        # 季集文字
        episode_text = ""
        if is_ep:
            s = info.season_id or ""
            e = info.episode_id or ""
            if s and e:
                episode_text = f"S{str(s).zfill(2)}E{str(e).zfill(2)}"
            elif s:
                episode_text = f"第 {s} 季"

        # 服务器名称
        server_name = info.channel.upper() if info.channel else "MediaServer"

        # 播放链接
        play_url = self._build_play_url(info)
        tmdb_url = f"https://www.themoviedb.org/{'tv' if is_ep else 'movie'}/{info.tmdb_id}" if info.tmdb_id else ""

        media = {
            "item_name": info.item_name or "",
            "item_type": info.item_type or "",
            "is_ep": is_ep,
            "status_text": status_text,
            "episode_text": episode_text,
            "overview": info.overview or "",
            "image_url": info.image_url or "",
            "server_name": server_name,
            "channel": info.channel or "",
            "play_url": play_url,
            "tmdb_url": tmdb_url,
            "season_id": info.season_id,
            "episode_id": info.episode_id,
        }

        if self._effective_tg_token and self._effective_tg_chat_id:
            self._send_telegram(media)
        if self._effective_wx_corp_id and self._effective_wx_corp_secret and self._effective_wx_agent_id:
            self._send_wechat(media)
        if self._bark_server and self._bark_keys:
            self._send_bark(media)

        # 保存最近 5 条卡片
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
            "item_name": media["item_name"],
            "item_type": media["item_type"],
            "season_id": info.season_id,
            "episode_id": info.episode_id,
            "image_url": info.image_url or "",
            "channel": info.channel or "",
            "channels": " / ".join(sent_channels),
        })
        self.save_data("recent_cards", cards[-5:])

    # ── 播放链接 ──────────────────────────────────────────────

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
        # server 直链
        base = self._emby_server_url
        if base and info.item_id:
            return f"{base}/web/index.html#!/item?id={info.item_id}"
        return base or ""

    # ── Telegram ─────────────────────────────────────────────

    def _send_telegram(self, media: dict):
        caption = f"<b>{media['server_name']} | {media['status_text']}</b>\n\n"
        caption += "─────────────────────\n\n"
        caption += f"<b>【{media['item_name']}】</b>\n"
        if media["episode_text"]:
            caption += f"{media['episode_text']} | 新更上线\n\n"
        else:
            caption += "\n"
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
                requests.post(f"{api}/bot{token}/sendPhoto", json={
                    "chat_id": chat_id, "photo": photo,
                    "caption": caption, "parse_mode": "HTML",
                }, timeout=15, proxies=self._proxies)
            else:
                requests.post(f"{api}/bot{token}/sendMessage", json={
                    "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
                }, timeout=15, proxies=self._proxies)
            logger.info(f"AWEmbyPush Telegram 发送成功：{media['item_name']}")
        except Exception as e:
            logger.error(f"AWEmbyPush Telegram 发送失败：{e}")

    # ── 企业微信 ──────────────────────────────────────────────

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
        agent_id = self._effective_wx_agent_id
        agent_id_val = int(agent_id) if str(agent_id).isdigit() else agent_id
        episode_text = media.get("episode_text", "") or "新更上线"

        try:
            url = f"{self._effective_wx_proxy_url}/cgi-bin/message/send?access_token={token}"
            if self._wx_msg_type == "news_notice":
                card = {
                    "card_type": "news_notice",
                    "source": {
                        "icon_url": "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png",
                        "desc": f"{media['server_name']} | {media['status_text']}",
                        "desc_color": 0,
                    },
                    "main_title": {"title": f"【{media['item_name']}】", "desc": episode_text},
                    "card_image": {"url": image_url, "aspect_ratio": 2.25},
                    "vertical_content_list": [
                        {"title": "📝 内容简介", "desc": _truncate(media.get("overview", ""), 120)},
                    ],
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
                payload = {
                    "touser": self._wx_user_id, "msgtype": "news", "agentid": agent_id_val,
                    "news": {"articles": [{"title": title_text,
                                           "description": _truncate(media.get("overview", ""), 100),
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

    # ── Bark ─────────────────────────────────────────────────

    def _send_bark(self, media: dict):
        body = ""
        if media.get("episode_text"):
            body += f"{media['episode_text']} | 新更上线\n"
        if media.get("overview"):
            body += f"\n📝 {_truncate(media['overview'], 80)}"
        url_target = (media.get("play_url") if (self._enable_watch_link and media.get("play_url"))
                      else media.get("tmdb_url", ""))
        payload = {
            "title": f"{media['server_name']} | {media['status_text']}\n【{media['item_name']}】",
            "body": body or "新内容已入库",
            "icon": "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/emby.png",
            "url": url_target,
            "device_keys": [k.strip() for k in self._bark_keys.split(",") if k.strip()],
        }
        try:
            res = requests.post(f"{self._bark_server}/push", json=payload,
                                timeout=15, proxies=self._proxies)
            if res.status_code == 200:
                logger.info(f"AWEmbyPush Bark 发送成功：{media['item_name']}")
            else:
                logger.error(f"AWEmbyPush Bark 发送失败：{res.status_code} {res.text}")
        except Exception as e:
            logger.error(f"AWEmbyPush Bark 发送异常：{e}")
