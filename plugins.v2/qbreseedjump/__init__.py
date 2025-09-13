from __future__ import annotations

import os
import re
import json
from datetime import datetime, timedelta, date
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo
from app.utils.http import RequestUtils


class qbreseedjump(_PluginBase):
    # 插件名称
    plugin_name = "QB跳校助手"
    # 插件描述
    plugin_desc = "仅支持QB下载器———————跳校有风险，操作需谨慎"
    # 插件图标
    plugin_icon = "seed.png"
    # 插件版本
    plugin_version = "1.0.3"
    # 插件作者
    plugin_author = "jzbqy"
    # 作者主页
    author_url = "https://github.com/jzbqy"
    # 插件配置项ID前缀
    plugin_config_prefix = "qbreseedjump_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 2

    _scheduler = None
    _event = Event()

    # 配置项
    _enabled = True
    _notify = False
    _onlyonce = False
    _cron = None
    _downloaders = []
    _pausedonly = True
    _includetags = "IYUU自动辅种"
    _includecategory = ""
    _processedtag = "已跳校"
    _autostart = True
    _remain_category = True
    _delete_exported = False
    _risk_confirmation = ""              # 风险确认文本
    _processedcategory = ""              # 处理完成后加分类
    _tracker_mapping = ""                # tracker映射表
    _show_tracker_mapping = False        # 是否显示tracker映射页面

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        try:
            if config:
                # 验证配置
                if not self.validate_config(config):
                    logger.error("配置验证失败，插件初始化中止")
                    return
                self._enabled = config.get("enabled", True)
                self._notify = config.get("notify", False)
                self._onlyonce = config.get("onlyonce", False)
                self._cron = config.get("cron", "0 0 */23 * *")
                # 规范化 cron：空或全星号时退回默认 0 0 */23 * *
                raw_cron = (self._cron or "").strip()
                if not raw_cron or raw_cron.replace(" ", "") in {"*****"} or raw_cron == "* * * * *":
                    self._cron = "0 0 */23 * *"
                    logger.info("检测到空或无效 cron，已回退为默认：0 0 */23 * *")
                else:
                    logger.info(f"使用配置的 cron: {self._cron}")
                self._downloaders = config.get("downloaders", [])
                self._pausedonly = config.get("pausedonly", True)
                self._includetags = config.get("includetags", "IYUU自动辅种")
                self._includecategory = config.get("includecategory", "")
                self._processedtag = config.get("processedtag", "已跳校")
                self._autostart = config.get("autostart", True)
                self._remain_category = config.get("remain_category", True)
                self._delete_exported = config.get("delete_exported", False)
                self._risk_confirmation = config.get("risk_confirmation", "")
                self._processedcategory = config.get("processedcategory", "")
                self._tracker_mapping = config.get("tracker_mapping", "")
                logger.info(f"加载tracker映射表: {len(self._tracker_mapping.split())} 条映射")
                
                # 保存配置
                self.__update_config()

            # 数据存储已改为使用MoviePilot内置API，无需初始化文件

            # 停止现有任务
            self.stop_service()

            if self._enabled:
                if self._onlyonce:
                    # 立即运行一次
                    try:
                        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                        self._scheduler.add_job(
                            func=self.reseed_all,
                            trigger='date',
                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3)
                        )
                        self._scheduler.start()
                        logger.info("自动跳校服务启动，立即运行一次")
                        
                        # 关闭一次性开关
                        self._onlyonce = False
                        # 保存配置
                        self.update_config({
                            "enabled": self._enabled,
                            "notify": self._notify,
                            "onlyonce": self._onlyonce,
                            "cron": self._cron,
                            "downloaders": self._downloaders,
                            "pausedonly": self._pausedonly,
                            "includetags": self._includetags,
                            "includecategory": self._includecategory,
                            "processedtag": self._processedtag,
                            "processedcategory": self._processedcategory,
                            "autostart": self._autostart,
                            "remain_category": self._remain_category,
                            "delete_exported": self._delete_exported,
                            "risk_confirmation": self._risk_confirmation,
                            "tracker_mapping": self._tracker_mapping
                        })
                    except Exception as e:
                        logger.error(f"启动定时任务失败: {e}")
                elif self._cron:
                    logger.info(f"自动跳校服务已配置 CRON '{self._cron}'，任务将通过公共服务注册")
        except Exception as e:
            logger.error(f"初始化插件失败: {e}")

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """获取命令"""
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """获取API"""
        return [
            {
                "path": "/clear_all_history_data",
                "endpoint": self.clear_all_history_data,
                "methods": ["GET"],
                "summary": "清理所有历史数据",
                "description": "清理所有历史统计数据"
            },
            {
                "path": "/reprocess_historical_data", 
                "endpoint": self.reprocess_historical_data,
                "methods": ["GET"],
                "summary": "重新处理历史数据",
                "description": "根据当前映射表重新处理历史数据"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """获取服务"""
        if self._enabled and self._cron:
            try:
                if str(self._cron).strip().count(" ") == 4:
                    return [{
                        "id": "Qbreseedjump",
                        "name": "自动跳校服务",
                        "trigger": CronTrigger.from_crontab(self._cron),
                        "func": self.reseed_all,
                        "kwargs": {}
                    }]
                else:
                    logger.error("QB跳校助手服务启动失败，cron格式错误")
            except Exception as err:
                logger.error(f"定时任务配置错误：{str(err)}")
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单"""
        try:
            downloader_options = [{"title": config.name, "value": config.name}
                                  for config in DownloaderHelper().get_configs().values()]
        except Exception:
            downloader_options = []
        
        # 获取API密钥用于前端调用
        import json
        js_safe_api_token = json.dumps(settings.API_TOKEN)
        # 生成 cron 的说明文字
        def _cron_text(expr: str) -> str:
            expr = (expr or "").strip() or "0 0 */23 * *"
            if expr == "0 0 */23 * *":
                return "每隔23小时执行一次"
            if expr == "0 0 * * *":
                return "每天 00:00 执行一次"
            if expr == "0 */1 * * *":
                return "每小时执行一次"
            if expr == "*/5 * * * *":
                return "每5分钟执行一次"
            # 解析自定义cron表达式
            try:
                parts = expr.split()
                if len(parts) == 5:
                    minute, hour, day, month, weekday = parts
                    if minute != "*" and hour != "*":
                        return f"每天 {hour.zfill(2)}:{minute.zfill(2)} 执行一次"
                    elif hour != "*":
                        return f"每天 {hour.zfill(2)}:00 执行一次"
                    elif minute != "*":
                        return f"每小时第 {minute} 分钟执行一次"
            except:
                pass
            return f"Cron: {expr}"
        cron_default = (self._cron or "").strip() or "0 0 */23 * *"
        cron_hint = _cron_text(cron_default)
            
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'enabled', 'label': '启用插件'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'notify', 'label': '发送通知'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'onlyonce', 'label': '立即运行一次'}
                                }]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VCronField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '执行周期',
                                        'placeholder': '0 0 */23 * *',
                                        'hint': cron_hint,
                                        'default': cron_default
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'multiple': True, 'chips': True, 'clearable': True,
                                        'model': 'downloaders', 'label': '下载器', 'items': downloader_options
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'pausedonly', 'label': '仅处理暂停任务'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'autostart', 'label': '添加后自动开始'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'remain_category', 'label': '保留原分类'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'delete_exported', 'label': '删除导出的种子文件'}
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {'model': 'includetags', 'label': '仅处理含这些标签(,分隔)'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {'model': 'includecategory', 'label': '仅处理这些分类(,分隔)'}
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {'model': 'processedtag', 'label': '处理完成后打标签'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {'model': 'processedcategory', 'label': '处理完成后加分类'}
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'div',
                                        'props': {'class': 'text-caption mb-2'},
                                        'content': [
                                            {
                                                'component': 'span',
                                                'text': '请确保完整填写：'
                                            },
                                            {
                                                'component': 'span',
                                                'props': {'style': 'font-weight: bold; color: red;'},
                                                'text': '我已知晓跳校可能带来的所有不良后果，且不会因此迁怒开发者'
                                            },
                                            {
                                                'component': 'span',
                                                'text': '（PS：实在生气可以骂AI，这句不用填）'
                                            }
                                        ],
                                        'attrs': {
                                            'title': '如果非要迁怒，可以尽情的把怒火发泄给AI，谢谢！'
                                        }
                                    },
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'risk_confirmation',
                                            'label': '风险确认（必填）',
                                            'placeholder': '我已知晓跳校可能带来的所有不良后果，且不会因此迁怒开发者',
                                            'required': True,
                                            'rules': [
                                                {
                                                    'required': True,
                                                    'message': '请填写完整的风险确认文本',
                                                    'validator': lambda v: v and v.strip() == '我已知晓跳校可能带来的所有不良后果，且不会因此迁怒开发者'
                                                }
                                            ]
                                        }
                                    },
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'tracker_mapping',
                                            'label': 'Tracker映射表（隐藏）',
                                            'style': 'display: none;'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # Tracker映射配置区域
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VCard',
                                        'props': {'variant': 'tonal'},
                                        'content': [
                                            {
                                                'component': 'VCardTitle',
                                                'text': 'Tracker映射表配置'
                                            },
                                            {
                                                'component': 'VCardText',
                                                'content': [
                                                    {
                                                        'component': 'div',
                                                        'props': {'class': 'text-caption text-grey mb-2'},
                                                        'text': '每行一个映射，格式：tracker域名:站点名称。保存后会自动重新处理历史数据。'
                                                    },
                                                    {
                                                        'component': 'VTextarea',
                                                        'props': {
                                                            'model': 'tracker_mapping',
                                                            'label': 'Tracker映射表',
                                                            'placeholder': 'agsvpt.trackers.work:末日\ntracker.agsvpt.work:末日\n...',
                                                            'rows': 15,
                                                            'hint': '每行一个映射，格式：tracker域名:站点名称'
                                                        }
                                                    },
                                                    {
                                                        'component': 'VRow',
                                                        'props': {'class': 'mt-2'},
                                                        'content': [
                                                            {
                                                                'component': 'VCol',
                                                                'props': {'cols': 12, 'md': 6},
                                                                'content': [{
                                                                    'component': 'VBtn',
                                                                    'props': {
                                                                        'variant': 'outlined',
                                                                        'color': 'primary',
                                                                        'onclick': f"""
                                                                        (async () => {{
                                                                            try {{
                                                                                const apiKey = {js_safe_api_token};
                                                                                const response = await fetch('/api/v1/plugin/Qbreseedjump/reprocess_historical_data?apikey=' + encodeURIComponent(apiKey));
                                                                                const result = await response.json();
                                                                                if (result.success) {{
                                                                                    alert('历史数据重新处理完成！');
                                                                                    location.reload();
                                                                                }} else {{
                                                                                    alert('重新处理失败：' + result.message);
                                                                                }}
                                                                            }} catch (error) {{
                                                                                alert('请求失败：' + error.message);
                                                                            }}
                                                                        }})()
                                                                        """
                                                                    },
                                                                    'text': '重新处理历史数据'
                                                                }]
                                                            },
                                                            {
                                                                'component': 'VCol',
                                                                'props': {'cols': 12, 'md': 6},
                                                                'content': [{
                                                                    'component': 'VBtn',
                                                                    'props': {
                                                                        'variant': 'outlined',
                                                                        'color': 'error',
                                                                        'onclick': f"""
                                                                        (async () => {{
                                                                            if (confirm('确定要清理所有历史数据吗？此操作不可恢复！')) {{
                                                                                try {{
                                                                                    const apiKey = {js_safe_api_token};
                                                                                    const response = await fetch('/api/v1/plugin/Qbreseedjump/clear_all_history_data?apikey=' + encodeURIComponent(apiKey));
                                                                                    const result = await response.json();
                                                                                    if (result.success) {{
                                                                                        alert('所有历史数据已清理完成！');
                                                                                        location.reload();
                                                                                    }} else {{
                                                                                        alert('清理失败：' + result.message);
                                                                                    }}
                                                                                }} catch (error) {{
                                                                                    alert('请求失败：' + error.message);
                                                                                }}
                                                                            }}
                                                                        }})()
                                                                        """
                                                                    },
                                                                    'text': '清理所有历史数据'
                                                                }]
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": False,
            "onlyonce": False,
            "cron": "0 0 */23 * *",
            "downloaders": [],
            "pausedonly": True,
            "includetags": "IYUU自动辅种",
            "includecategory": "",
            "processedtag": "已跳校",
            "processedcategory": "",
            "autostart": True,
            "remain_category": True,
        "delete_exported": False,
        "risk_confirmation": "",
        "tracker_mapping": "agsvpt.trackers.work:末日\ntracker.agsvpt.work:末日\ntracker.agsvpt.cn:末日\ntracker.carpt.net:车站\ntracker.cyanbug.net:大青虫\ntracker.greatposterwall.com:海豹\ntracker.ilolicon.cc:萝莉\ntracker01.ilovelemonhd.me:柠檬\nourbits.club:我堡\npt.ourhelp.club:我堡\nptl.gs:劳改所\nrelay01.ptl.gs:8443:劳改所\nrousi.zip:肉丝\ntracker.rousipt.com:肉丝\ntracker.yemapt.org:野马\npt.gtk.pw:GTK\nwww.pttime.org:PTT\nnextpt.net:FSM\nconnects.icu:FSM\npt.gtkpw.xyz:GTK\ntracker.ptchdbits.co:彩虹岛\ntracker.rainbowisland.co:彩虹岛\nchdbits.xyz:彩虹岛\nzmpt.cc:织梦\nzmpt.club:织梦\ntracker.hdsky.me:天空\ntra1.m-team.cc:馒头\ntracker.pterclub.com:猫站\nhdfans.org:红豆饭\non.springsunday.net:春天\ntracker.totheglory.im:套套哥\nt.hddolby.com:高清杜比\nt.audiences.me:观众\ntracker.piggo.me:猪猪\ntracker.hdarea.club:高清视界"
        }

    def validate_config(self, config: dict) -> bool:
        """验证配置"""
        try:
            # 验证风险确认文本
            required_text = "我已知晓跳校可能带来的所有不良后果，且不会因此迁怒开发者"
            risk_confirmation = config.get("risk_confirmation", "").strip()
            if not risk_confirmation or risk_confirmation != required_text:
                logger.error(f"风险确认未通过，请正确填写确认文本：{required_text}")
                return False
            
            # 验证其他必要配置
            if not config.get("downloaders"):
                logger.error("请至少选择一个下载器")
                return False
                
            return True
        except Exception as e:
            logger.error(f"配置验证失败: {e}")
            return False

    def get_state(self) -> bool:
        """获取插件状态"""
        return self._enabled

    def __update_config(self):
        """保存配置"""
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "downloaders": self._downloaders,
            "pausedonly": self._pausedonly,
            "includetags": self._includetags,
            "includecategory": self._includecategory,
            "processedtag": self._processedtag,
            "processedcategory": self._processedcategory,
            "autostart": self._autostart,
            "remain_category": self._remain_category,
            "delete_exported": self._delete_exported,
            "risk_confirmation": self._risk_confirmation,
            "tracker_mapping": self._tracker_mapping
        })

    def stop_service(self):
        """停止服务"""
        try:
            if self._scheduler:
                self._scheduler.shutdown()
                self._scheduler = None
            logger.info("跳校服务已停止")
        except Exception as e:
            logger.error(f"停止跳校服务失败: {e}")

    def get_page(self) -> List[dict]:
        """获取详情页面"""
        try:
            # 检查是否是tracker映射页面（通过URL参数或属性）
            import os
            if (hasattr(self, '_show_tracker_mapping') and self._show_tracker_mapping) or \
               (os.environ.get('REQUEST_URI', '').find('action=tracker_mapping') != -1):
                return self._get_tracker_mapping_page()
            
            stats = self._load_stats()
            if not stats.get("daily") and not stats.get("total"):
                return [
                    {
                        'component': 'div',
                        'text': '跳校统计 - 插件运行正常，暂无统计数据',
                        'props': {'class': 'text-center pa-4',}
                    }
                ]
            
            # 获取今日日期
            today = date.today().strftime("%Y-%m-%d")
            
            # 计算今日统计
            total_today = 0
            volume_today = 0
            if today in stats.get("daily", {}):
                for downloader, data in stats["daily"][today].items():
                    total_today += data.get("success", 0) + data.get("failed", 0)
                    if "volumes" in data:
                        logger.info(f"今日 {downloader} 体积数据: {data['volumes']}")
                        for tracker, volume in data["volumes"].items():
                            volume_today += volume
                    else:
                        logger.info(f"今日 {downloader} 没有体积数据")
            
            # 计算累计统计
            total_all = 0
            volume_all = 0
            if stats.get("total"):
                for downloader, data in stats["total"].items():
                    total_all += data.get("success", 0) + data.get("failed", 0)
                    if "volumes" in data:
                        logger.info(f"累计 {downloader} 体积数据: {data['volumes']}")
                        for tracker, volume in data["volumes"].items():
                            volume_all += volume
                    else:
                        logger.info(f"累计 {downloader} 没有体积数据")
            
            # 格式化体积显示
            def format_size(size_bytes):
                if size_bytes < 1024 * 1024 * 1024:  # < 1GB
                    return f"{size_bytes / (1024 * 1024):.1f}MB"
                elif size_bytes < 1024 * 1024 * 1024 * 1024:  # < 1TB
                    return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
                else:
                    return f"{size_bytes / (1024 * 1024 * 1024 * 1024):.1f}TB"
            
            logger.info(f"体积统计 - 今日: {volume_today} bytes, 累计: {volume_all} bytes")
            
            # 构建顶部统计卡片
            stat_cards = [
                # 今日跳校数
                {
                    'component': 'VCol',
                    'props': {'cols': 6, 'md': 3},
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {'variant': 'tonal'},
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {'class': 'd-flex align-center'},
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {'src': '/plugin_icon/statistic.png'}
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '今日跳校数'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'd-flex align-center flex-wrap'},
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {'class': 'text-h6'},
                                                            'text': str(total_today)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
                # 今日跳校体积
                {
                    'component': 'VCol',
                    'props': {'cols': 6, 'md': 3},
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {'variant': 'tonal'},
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {'class': 'd-flex align-center'},
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {'src': '/plugin_icon/upload.png'}
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '今日跳校体积'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'd-flex align-center flex-wrap'},
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {'class': 'text-h6 text-success'},
                                                            'text': format_size(volume_today)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
                # 总跳校数
                {
                    'component': 'VCol',
                    'props': {'cols': 6, 'md': 3},
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {'variant': 'tonal'},
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {'class': 'd-flex align-center'},
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {'src': '/plugin_icon/download.png'}
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '总跳校数'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'd-flex align-center flex-wrap'},
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {'class': 'text-h6 text-error'},
                                                            'text': str(total_all)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
                # 总跳校体积
                {
                    'component': 'VCol',
                    'props': {'cols': 6, 'md': 3},
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {'variant': 'tonal'},
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {'class': 'd-flex align-center'},
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {'src': '/plugin_icon/seed.png'}
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {'class': 'text-caption'},
                                                    'text': '总跳校体积'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'd-flex align-center flex-wrap'},
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {'class': 'text-h6'},
                                                            'text': format_size(volume_all)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
            
            # 构建饼状图数据
            chart_elements = []
            
            # 今日各站点跳校数占比饼状图
            if today in stats.get("daily", {}):
                tracker_count_data = []
                tracker_count_labels = []
                for downloader, data in stats["daily"][today].items():
                    if "trackers" in data:
                        for tracker, count in data["trackers"].items():
                            if tracker in tracker_count_labels:
                                idx = tracker_count_labels.index(tracker)
                                tracker_count_data[idx] += count
                            else:
                                tracker_count_labels.append(tracker)
                                tracker_count_data.append(count)
                
                if tracker_count_data:
                    chart_elements.append({
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 6},
                        'content': [
                            {
                                'component': 'VApexChart',
                                'props': {
                                    'height': 300,
                                    'options': {
                                        'chart': {
                                            'type': 'pie',
                                        },
                                        'labels': tracker_count_labels,
                                        'title': {
                                            'text': f'今日各站点跳校数占比（{today}）共 {sum(tracker_count_data)} 个任务'
                                        },
                                        'legend': {
                                            'show': True
                                        },
                                        'plotOptions': {
                                            'pie': {
                                                'expandOnClick': False
                                            }
                                        },
                                        'noData': {
                                            'text': '暂无数据'
                                        }
                                    },
                                    'series': tracker_count_data
                                }
                            }
                        ]
                    })
            
            # 今日各站点跳校体积占比饼状图
            if today in stats.get("daily", {}):
                tracker_volume_data = []
                tracker_volume_labels = []
                for downloader, data in stats["daily"][today].items():
                    if "volumes" in data:
                        for tracker, volume in data["volumes"].items():
                            if tracker in tracker_volume_labels:
                                idx = tracker_volume_labels.index(tracker)
                                tracker_volume_data[idx] += volume
                            else:
                                tracker_volume_labels.append(tracker)
                                tracker_volume_data.append(volume)
                
                if tracker_volume_data:
                    # 转换体积数据为GB显示
                    volume_data_gb = [v / (1024 * 1024 * 1024) for v in tracker_volume_data]
                    chart_elements.append({
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 6},
                        'content': [
                            {
                                'component': 'VApexChart',
                                'props': {
                                    'height': 300,
                                    'options': {
                                        'chart': {
                                            'type': 'pie',
                                        },
                                        'labels': tracker_volume_labels,
                                        'title': {
                                            'text': f'今日各站点跳校体积占比（{today}）共 {format_size(sum(tracker_volume_data))}'
                                        },
                                        'legend': {
                                            'show': True
                                        },
                                        'plotOptions': {
                                            'pie': {
                                                'expandOnClick': False
                                            }
                                        },
                                        'noData': {
                                            'text': '暂无数据'
                                        }
                                    },
                                    'series': volume_data_gb
                                }
                            }
                        ]
                    })
            
            # 总各站点跳校数占比饼状图
            if stats.get("total"):
                total_tracker_count_data = []
                total_tracker_count_labels = []
                for downloader, data in stats["total"].items():
                    if "trackers" in data:
                        for tracker, count in data["trackers"].items():
                            if tracker in total_tracker_count_labels:
                                idx = total_tracker_count_labels.index(tracker)
                                total_tracker_count_data[idx] += count
                            else:
                                total_tracker_count_labels.append(tracker)
                                total_tracker_count_data.append(count)
                
                if total_tracker_count_data:
                    chart_elements.append({
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 6},
                        'content': [
                            {
                                'component': 'VApexChart',
                                'props': {
                                    'height': 300,
                                    'options': {
                                        'chart': {
                                            'type': 'pie',
                                        },
                                        'labels': total_tracker_count_labels,
                                        'title': {
                                            'text': f'总各站点跳校数占比 共 {sum(total_tracker_count_data)} 个任务'
                                        },
                                        'legend': {
                                            'show': True
                                        },
                                        'plotOptions': {
                                            'pie': {
                                                'expandOnClick': False
                                            }
                                        },
                                        'noData': {
                                            'text': '暂无数据'
                                        }
                                    },
                                    'series': total_tracker_count_data
                                }
                            }
                        ]
                    })
            
            # 总各站点跳校体积占比饼状图
            if stats.get("total"):
                total_tracker_volume_data = []
                total_tracker_volume_labels = []
                for downloader, data in stats["total"].items():
                    if "volumes" in data:
                        for tracker, volume in data["volumes"].items():
                            if tracker in total_tracker_volume_labels:
                                idx = total_tracker_volume_labels.index(tracker)
                                total_tracker_volume_data[idx] += volume
                            else:
                                total_tracker_volume_labels.append(tracker)
                                total_tracker_volume_data.append(volume)
                
                if total_tracker_volume_data:
                    # 转换体积数据为GB显示
                    total_volume_data_gb = [v / (1024 * 1024 * 1024) for v in total_tracker_volume_data]
                    chart_elements.append({
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 6},
                        'content': [
                            {
                                'component': 'VApexChart',
                                'props': {
                                    'height': 300,
                                    'options': {
                                        'chart': {
                                            'type': 'pie',
                                        },
                                        'labels': total_tracker_volume_labels,
                                        'title': {
                                            'text': f'总各站点跳校体积占比 共 {format_size(sum(total_tracker_volume_data))}'
                                        },
                                        'legend': {
                                            'show': True
                                        },
                                        'plotOptions': {
                                            'pie': {
                                                'expandOnClick': False
                                            }
                                        },
                                        'noData': {
                                            'text': '暂无数据'
                                        }
                                    },
                                    'series': total_volume_data_gb
                                }
                            }
                        ]
                    })
            
            return [
                # 顶部统计卡片
                {
                    'component': 'VRow',
                    'content': stat_cards
                },
                # 饼状图区域
                {
                    'component': 'VRow',
                    'content': chart_elements
                }
            ]
        except Exception as e:
            logger.error(f"获取统计页面失败: {e}")
            return [
                {
                    'component': 'div',
                    'text': f'跳校统计 - 加载失败: {str(e)}',
                    'props': {'class': 'text-center pa-4',}
                }
            ]

    def _load_stats(self) -> Dict:
        """加载统计数据"""
        try:
            stats = self.get_data("stats")
            if not stats:
                return {"daily": {}, "total": {}}
            # 确保数据结构完整
            if "daily" not in stats:
                stats["daily"] = {}
            if "total" not in stats:
                stats["total"] = {}
            
            # 清理历史数据中的"未知站点"
            self._clean_unknown_sites(stats)
            
            return stats
        except Exception as e:
            logger.error(f"加载统计数据失败: {e}")
            return {"daily": {}, "total": {}}

    def _clean_unknown_sites(self, stats: Dict):
        """清理历史数据中的未知站点"""
        try:
            logger.info("开始清理历史数据中的未知站点")
            cleaned_count = 0
            
            # 清理每日数据中的未知站点
            for date_key, date_data in stats.get("daily", {}).items():
                for downloader_name, downloader_data in date_data.items():
                    # 清理trackers中的未知站点
                    if "trackers" in downloader_data and "未知站点" in downloader_data["trackers"]:
                        del downloader_data["trackers"]["未知站点"]
                        cleaned_count += 1
                        logger.info(f"清理每日数据中的未知站点: {date_key} - {downloader_name}")
                    
                    # 清理volumes中的未知站点
                    if "volumes" in downloader_data and "未知站点" in downloader_data["volumes"]:
                        del downloader_data["volumes"]["未知站点"]
                        cleaned_count += 1
                        logger.info(f"清理每日数据中的未知站点体积: {date_key} - {downloader_name}")
            
            # 清理累计数据中的未知站点
            for downloader_name, downloader_data in stats.get("total", {}).items():
                # 清理trackers中的未知站点
                if "trackers" in downloader_data and "未知站点" in downloader_data["trackers"]:
                    del downloader_data["trackers"]["未知站点"]
                    cleaned_count += 1
                    logger.info(f"清理累计数据中的未知站点: {downloader_name}")
                
                # 清理volumes中的未知站点
                if "volumes" in downloader_data and "未知站点" in downloader_data["volumes"]:
                    del downloader_data["volumes"]["未知站点"]
                    cleaned_count += 1
                    logger.info(f"清理累计数据中的未知站点体积: {downloader_name}")
            
            logger.info(f"清理完成，共清理了 {cleaned_count} 个未知站点记录")
            
            # 保存清理后的数据
            if cleaned_count > 0:
                self._save_stats(stats)
                logger.info("已保存清理后的统计数据")
            
        except Exception as e:
            logger.error(f"清理未知站点失败: {e}")

    def _save_stats(self, stats: Dict):
        """保存统计数据"""
        try:
            self.save_data("stats", stats)
        except Exception as e:
            logger.error(f"保存统计数据失败: {e}")

    def _parse_tracker_mapping(self) -> Dict[str, str]:
        """解析tracker映射表"""
        mapping = {}
        if not self._tracker_mapping:
            return mapping
        
        try:
            for line in self._tracker_mapping.strip().split('\n'):
                line = line.strip()
                if ':' in line:
                    tracker, site_name = line.split(':', 1)
                    mapping[tracker.strip()] = site_name.strip()
        except Exception as e:
            logger.error(f"解析tracker映射表失败: {e}")
        
        return mapping

    def _get_site_name_from_tracker(self, tracker_url: str) -> str:
        """根据 tracker 通过映射表或正则推断站点名称。
        优先映射表；无匹配则取域名的二级名作为站点名；失败返回 '其他'。"""
        if not tracker_url:
            return '其他'

        logger.info(f"开始解析tracker: {tracker_url}")

        # 1) 映射表优先
        mapping = self._parse_tracker_mapping() or {}
        logger.info(f"映射表内容: {mapping}")
        
        for tracker, site_name in mapping.items():
            try:
                logger.info(f"尝试匹配: tracker='{tracker}' in tracker_url='{tracker_url}'")
                if tracker and tracker in tracker_url:
                    logger.info(f"映射表匹配成功: {tracker} -> {site_name}")
                    return site_name
                else:
                    logger.info(f"映射表匹配失败: {tracker} not in {tracker_url}")
            except Exception as e:
                logger.warning(f"映射表匹配异常: {tracker} -> {e}")
                continue

        # 2) 正则推断域名的二级名（忽略常见前缀）
        try:
            import re
            # 提取 host
            host_match = re.search(r'(?:(?:https?|udp)://)?([^/:]+)', tracker_url)
            host = host_match.group(1) if host_match else tracker_url
            logger.info(f"提取的host: {host}")
            
            labels = host.split('.')
            logger.info(f"host分割结果: {labels}")
            
            # 去掉常见前缀
            prefixes = {"tracker", "www", "tra1", "t", "relay01"}
            labels = [l for l in labels if l not in prefixes]
            logger.info(f"去除前缀后: {labels}")
            
            if len(labels) >= 2:
                site_key = labels[-2]
                logger.info(f"正则解析成功: {site_key}")
                return site_key
        except Exception as e:
            logger.warning(f"正则解析异常: {e}")

        # 3) 仍失败
        logger.info(f"未能从映射或正则识别站点，tracker={tracker_url}")
        return '其他站点'

    def _get_tracker_mapping_page(self) -> List[dict]:
        """获取tracker映射页面"""
        return [
            {
                'component': 'VCard',
                'props': {'variant': 'tonal'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'text': 'Tracker映射表配置'
                    },
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'div',
                                'props': {'class': 'text-caption text-grey mb-2'},
                                'text': '每行一个映射，格式：tracker域名:站点名称。保存后会自动重新处理历史数据。'
                            },
                            {
                                'component': 'VTextarea',
                                'props': {
                                    'model': 'tracker_mapping',
                                    'label': 'Tracker映射表',
                                    'placeholder': 'agsvpt.trackers.work:末日\ntracker.agsvpt.work:末日\n...',
                                    'rows': 15,
                                    'hint': '每行一个映射，格式：tracker域名:站点名称',
                                    'value': self._tracker_mapping
                                }
                            },
                            {
                                'component': 'div',
                                'props': {'class': 'd-flex justify-space-between mt-4'},
                                'content': [
                                    {
                                        'component': 'VBtn',
                                        'props': {
                                            'variant': 'outlined',
                                            'color': 'secondary',
                                            'href': '/plugin/page/Qbreseedjump'
                                        },
                                        'text': '返回统计'
                                    },
                                    {
                                        'component': 'VBtn',
                                        'props': {
                                            'variant': 'outlined',
                                            'color': 'primary',
                                            'onclick': 'save_tracker_mapping'
                                        },
                                        'text': '保存映射表'
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def save_tracker_mapping(self, mapping_text: str = None):
        """保存tracker映射表"""
        try:
            if mapping_text is not None:
                self._tracker_mapping = mapping_text
            logger.info(f"Tracker映射表已更新: {len(self._tracker_mapping.split())} 条映射")
            
            # 重新处理历史数据
            logger.info("开始重新处理历史数据...")
            self._reprocess_historical_data()
            logger.info("历史数据重新处理完成")
            return True
        except Exception as e:
            logger.error(f"保存tracker映射表失败: {e}")
            return False

    def reprocess_historical_data(self):
        """手动触发重新处理历史数据"""
        try:
            logger.info("手动触发重新处理历史数据...")
            self._reprocess_historical_data()
            logger.info("手动重新处理历史数据完成")
            return {"success": True, "message": "历史数据重新处理完成"}
        except Exception as e:
            logger.error(f"手动重新处理历史数据失败: {e}")
            return {"success": False, "message": f"重新处理失败: {str(e)}"}

    def clear_all_history_data(self):
        """清理所有历史数据"""
        try:
            logger.info("开始清理所有历史数据...")
            
            # 清空所有统计数据
            empty_stats = {"daily": {}, "total": {}}
            self._save_stats(empty_stats)
            
            logger.info("所有历史数据已清理完成")
            return {"success": True, "message": "所有历史数据已清理完成"}
        except Exception as e:
            logger.error(f"清理所有历史数据失败: {e}")
            return {"success": False, "message": f"清理失败: {str(e)}"}

    def _reprocess_historical_data(self):
        """重新处理历史数据，更新站点名称"""
        try:
            logger.info("开始重新处理历史数据...")
            stats = self._load_stats()
            updated = False
            
            # 记录当前映射表
            mapping = self._parse_tracker_mapping()
            logger.info(f"当前映射表: {mapping}")
            
            # 处理每日数据
            daily_count = 0
            for date_key, daily_data in stats.get("daily", {}).items():
                for downloader, data in daily_data.items():
                    if "volumes" in data and "trackers" in data:
                        logger.info(f"处理每日数据: {date_key} - {downloader}")
                        logger.info(f"原始volumes: {data['volumes']}")
                        logger.info(f"原始trackers: {data['trackers']}")
                        
                        # 重新映射tracker名称
                        new_volumes = {}
                        new_trackers = {}
                        
                        for tracker, volume in data["volumes"].items():
                            # 直接使用tracker名称进行映射，不需要重新获取种子信息
                            new_site_name = self._get_site_name_from_tracker(tracker)
                            logger.info(f"映射 {tracker} -> {new_site_name}")
                            new_volumes[new_site_name] = new_volumes.get(new_site_name, 0) + volume
                        
                        for tracker, count in data["trackers"].items():
                            new_site_name = self._get_site_name_from_tracker(tracker)
                            logger.info(f"映射 {tracker} -> {new_site_name}")
                            new_trackers[new_site_name] = new_trackers.get(new_site_name, 0) + count
                        
                        logger.info(f"新volumes: {new_volumes}")
                        logger.info(f"新trackers: {new_trackers}")
                        
                        # 过滤未知/其他
                        if '未知站点' in new_volumes:
                            del new_volumes['未知站点']
                        if '其他' in new_volumes:
                            del new_volumes['其他']
                        if '未知站点' in new_trackers:
                            del new_trackers['未知站点']
                        if '其他' in new_trackers:
                            del new_trackers['其他']

                        # 更新数据
                        data["volumes"] = new_volumes
                        data["trackers"] = new_trackers
                        updated = True
                        daily_count += 1
            
            # 处理累计数据
            total_count = 0
            for downloader, data in stats.get("total", {}).items():
                if "volumes" in data and "trackers" in data:
                    logger.info(f"处理累计数据: {downloader}")
                    logger.info(f"原始volumes: {data['volumes']}")
                    logger.info(f"原始trackers: {data['trackers']}")
                    
                    # 重新映射tracker名称
                    new_volumes = {}
                    new_trackers = {}
                    
                    for tracker, volume in data["volumes"].items():
                        new_site_name = self._get_site_name_from_tracker(tracker)
                        logger.info(f"映射 {tracker} -> {new_site_name}")
                        new_volumes[new_site_name] = new_volumes.get(new_site_name, 0) + volume
                    
                    for tracker, count in data["trackers"].items():
                        new_site_name = self._get_site_name_from_tracker(tracker)
                        logger.info(f"映射 {tracker} -> {new_site_name}")
                        new_trackers[new_site_name] = new_trackers.get(new_site_name, 0) + count
                    
                    logger.info(f"新volumes: {new_volumes}")
                    logger.info(f"新trackers: {new_trackers}")
                    
                    # 过滤未知/其他
                    if '未知站点' in new_volumes:
                        del new_volumes['未知站点']
                    if '其他' in new_volumes:
                        del new_volumes['其他']
                    if '未知站点' in new_trackers:
                        del new_trackers['未知站点']
                    if '其他' in new_trackers:
                        del new_trackers['其他']

                    # 更新数据
                    data["volumes"] = new_volumes
                    data["trackers"] = new_trackers
                    updated = True
                    total_count += 1
            
            if updated:
                self._save_stats(stats)
                logger.info(f"历史数据已重新处理，站点名称已更新 - 处理了 {daily_count} 个每日数据，{total_count} 个累计数据")
            else:
                logger.info("没有需要更新的历史数据")
                
        except Exception as e:
            logger.error(f"重新处理历史数据失败: {e}")

    def _update_stats(self, downloader_name: str, success_count: int, failed_count: int, 
                     tracker_info: dict = None, volume_info: dict = None):
        """更新统计数据"""
        try:
            stats = self._load_stats()
            today = date.today().strftime("%Y-%m-%d")
            
            # 更新今日统计
            if today not in stats["daily"]:
                stats["daily"][today] = {}
            if downloader_name not in stats["daily"][today]:
                stats["daily"][today][downloader_name] = {"success": 0, "failed": 0, "trackers": {}, "volumes": {}}
            
            stats["daily"][today][downloader_name]["success"] += success_count
            stats["daily"][today][downloader_name]["failed"] += failed_count
            
            # 更新tracker统计（数量）
            if tracker_info:
                for tracker, count in tracker_info.items():
                    if not tracker or tracker == "未知站点":
                        continue
                    if "trackers" not in stats["daily"][today][downloader_name]:
                        stats["daily"][today][downloader_name]["trackers"] = {}
                    if tracker not in stats["daily"][today][downloader_name]["trackers"]:
                        stats["daily"][today][downloader_name]["trackers"][tracker] = 0
                    stats["daily"][today][downloader_name]["trackers"][tracker] += count
            
            # 更新tracker体积统计
            if volume_info:
                for tracker, volume in volume_info.items():
                    if not tracker or tracker == "未知站点":
                        continue
                    if "volumes" not in stats["daily"][today][downloader_name]:
                        stats["daily"][today][downloader_name]["volumes"] = {}
                    if tracker not in stats["daily"][today][downloader_name]["volumes"]:
                        stats["daily"][today][downloader_name]["volumes"][tracker] = 0
                    stats["daily"][today][downloader_name]["volumes"][tracker] += volume
            
            # 更新累计统计
            if downloader_name not in stats["total"]:
                stats["total"][downloader_name] = {"success": 0, "failed": 0, "trackers": {}, "volumes": {}}
            
            stats["total"][downloader_name]["success"] += success_count
            stats["total"][downloader_name]["failed"] += failed_count
            
            # 更新累计tracker统计（数量）
            if tracker_info:
                for tracker, count in tracker_info.items():
                    if not tracker or tracker == "未知站点":
                        continue
                    if "trackers" not in stats["total"][downloader_name]:
                        stats["total"][downloader_name]["trackers"] = {}
                    if tracker not in stats["total"][downloader_name]["trackers"]:
                        stats["total"][downloader_name]["trackers"][tracker] = 0
                    stats["total"][downloader_name]["trackers"][tracker] += count
            
            # 更新累计tracker体积统计
            if volume_info:
                for tracker, volume in volume_info.items():
                    if not tracker or tracker == "未知站点":
                        continue
                    if "volumes" not in stats["total"][downloader_name]:
                        stats["total"][downloader_name]["volumes"] = {}
                    if tracker not in stats["total"][downloader_name]["volumes"]:
                        stats["total"][downloader_name]["volumes"][tracker] = 0
                    stats["total"][downloader_name]["volumes"][tracker] += volume
            
            # 清理30天前的数据
            cutoff_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
            stats["daily"] = {k: v for k, v in stats["daily"].items() if k >= cutoff_date}
            
            self._save_stats(stats)
            logger.info(f"统计数据已更新: {downloader_name} - 成功 {success_count}, 失败 {failed_count}")
        except Exception as e:
            logger.error(f"更新统计数据失败: {e}")

    def reseed_all(self):
        """执行跳校任务"""
        # 验证风险确认
        required_text = "我已知晓跳校可能带来的所有不良后果，且不会因此迁怒开发者"
        if not self._risk_confirmation or self._risk_confirmation.strip() != required_text:
            logger.error(f"风险确认未通过，请正确填写确认文本：{required_text}")
            return
        
        if not self._downloaders:
            logger.warning("未配置下载器，跳过跳校任务")
            return

        downloader_helper = DownloaderHelper()
        services = downloader_helper.get_services(name_filters=self._downloaders)
        
        if not services:
            logger.warning("获取下载器服务失败，跳过跳校任务")
            return

        total_candidates = 0
        total_success = 0
        total_failed = 0

        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"下载器 {service_name} 未连接，跳过")
                continue

            candidates, success, failed = self._reseed_service(service_info)
            total_candidates += candidates
            total_success += success
            total_failed += failed

        logger.info(f"跳校任务完成：总任务 {total_candidates}，成功 {total_success}，失败 {total_failed}")

        if self._notify and (total_success > 0 or total_failed > 0):
            try:
                logger.info(f"准备发送通知: _notify={self._notify}, total_success={total_success}, total_failed={total_failed}")
                
                # 从统计数据中获取今日和总的跳校信息
                stats = self._load_stats()
                today = date.today().strftime("%Y-%m-%d")
                
                # 计算今日跳校数量和体积
                today_count = 0
                today_volume = 0
                if today in stats.get("daily", {}):
                    for downloader, data in stats["daily"][today].items():
                        today_count += data.get("success", 0) + data.get("failed", 0)
                        if "volumes" in data:
                            today_volume += sum(data["volumes"].values())
                
                # 计算总跳校数量和体积
                total_count = 0
                total_volume = 0
                if stats.get("total"):
                    for downloader, data in stats["total"].items():
                        total_count += data.get("success", 0) + data.get("failed", 0)
                        if "volumes" in data:
                            total_volume += sum(data["volumes"].values())
                
                # 格式化体积显示
                def format_volume(bytes_val):
                    if bytes_val < 1024 * 1024 * 1024:  # < 1GB
                        return f"{bytes_val / (1024 * 1024):.1f}MB"
                    elif bytes_val < 1024 * 1024 * 1024 * 1024:  # < 1TB
                        return f"{bytes_val / (1024 * 1024 * 1024):.1f}GB"
                    else:
                        return f"{bytes_val / (1024 * 1024 * 1024 * 1024):.1f}TB"
                
                notification_text = f"━━━━━━━━━━━━━━\n\n" \
                                   f"📊 今日跳校数量：{today_count}\n" \
                                   f"📦 今日跳校体积：{format_volume(today_volume) if today_volume > 0 else '0B'}\n" \
                                   f"📈 历史跳校数量：{total_count}\n" \
                                   f"📦 历史跳校体积：{format_volume(total_volume) if total_volume > 0 else '0B'}\n\n" \
                                   f"━━━━━━━━━━━━━━\n" \
                                   f"⏰ 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
                logger.info(f"通知内容长度: {len(notification_text)}")
                logger.info(f"通知内容预览: {notification_text[:100]}...")
                
                # 发送通知
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="【QB跳校助手】任务完成",
                    text=notification_text
                )
                logger.info("通知发送完成")
            except Exception as e:
                logger.error(f"发送通知失败: {e}")
        else:
            logger.info(f"跳过通知发送: _notify={self._notify}, total_success={total_success}, total_failed={total_failed}")

    def _reseed_service(self, service_info: ServiceInfo):
        """处理单个下载器的跳校任务"""
        service = service_info.instance
        candidates = []
        success = 0
        failed = 0

        try:
            # 获取种子列表
            torrents, error = service.get_torrents()
            if error or not torrents:
                logger.warning(f"[{service_info.name}] 获取种子列表失败")
                return 0, 0, 0

            logger.info(f"[{service_info.name}] 获取到 {len(torrents)} 个种子")

            # 筛选候选种子
            for torrent in torrents:
                if self._is_candidate(torrent, service_info):
                    candidates.append(torrent)

            logger.info(f"[{service_info.name}] 找到 {len(candidates)} 个候选种子")

            # 处理每个候选种子
            all_tracker_info = {}
            all_volume_info = {}
            
            for torrent in candidates:
                try:
                    success_flag, tracker_info, volume_info = self._reseed_torrent(torrent, service_info)
                    if success_flag:
                        success += 1
                    else:
                        failed += 1
                    
                    # 合并tracker信息（数量）
                    for tracker, count in tracker_info.items():
                        if tracker not in all_tracker_info:
                            all_tracker_info[tracker] = 0
                        all_tracker_info[tracker] += count
                    
                    # 合并体积信息
                    logger.info(f"[{service_info.name}] 合并体积信息: {volume_info}")
                    for tracker, volume in volume_info.items():
                        if tracker not in all_volume_info:
                            all_volume_info[tracker] = 0
                        all_volume_info[tracker] += volume
                    logger.info(f"[{service_info.name}] 合并后体积信息: {all_volume_info}")
                        
                except Exception as e:
                    logger.error(f"[{service_info.name}] 处理种子失败: {e}")
                    failed += 1

            logger.info(f"[{service_info.name}] 完成：成功 {success}，失败 {failed}，总计 {len(candidates)}")

            # 更新统计数据
            if success > 0 or failed > 0:
                self._update_stats(service_info.name, success, failed, all_tracker_info, all_volume_info)

            return len(candidates), success, failed

        except Exception as e:
            logger.error(f"[{service_info.name}] 处理失败: {e}")
            return len(candidates), success, failed

    def _is_candidate(self, torrent, service_info: ServiceInfo) -> bool:
        """判断是否为候选种子"""
        try:
            torrent_name = getattr(torrent, 'name', 'Unknown')
            
            # 检查暂停状态
            if self._pausedonly:
                # 只处理真正暂停的状态，不包括未活跃但未暂停的状态
                paused_states = ["pausedUP", "pausedDL"]
                if torrent.state not in paused_states:
                    return False

            # 检查标签
            if self._includetags and hasattr(torrent, 'tags') and torrent.tags:
                tags = [tag.strip() for tag in torrent.tags.split(',')]
                include_tags = [tag.strip() for tag in self._includetags.split(',')]
                if not any(tag in tags for tag in include_tags):
                    return False

            # 检查分类
            if self._includecategory and torrent.category:
                include_categories = [cat.strip() for cat in self._includecategory.split(',')]
                if torrent.category not in include_categories:
                    return False
            return True

        except Exception as e:
            logger.error(f"检查候选种子失败: {e}")
            return False

    def _reseed_torrent(self, torrent, service_info: ServiceInfo) -> tuple[bool, dict, dict]:
        """处理单个种子的跳校"""
        try:
            torrent_hash = torrent.hash
            torrent_name = torrent.name
            save_path = torrent.save_path
            category = torrent.category
            tags = torrent.tags.split(',') if torrent.tags else []

            logger.info(f"[{service_info.name}] 开始处理种子: {torrent_name}")
            
            # 收集tracker信息
            tracker_info = {}
            volume_info = {}
            
            # 收集体积信息（无论是否有tracker都要收集）
            torrent_size = 0
            # 尝试多种可能的size属性名
            for size_attr in ['size', 'total_size', 'size_bytes']:
                if hasattr(torrent, size_attr):
                    torrent_size = getattr(torrent, size_attr)
                    if torrent_size:
                        break
            
            logger.info(f"[{service_info.name}] 种子属性: {[attr for attr in dir(torrent) if not attr.startswith('_')]}")
            logger.info(f"[{service_info.name}] 种子大小: {torrent_size} bytes")
            
            if torrent_size:
                # 尝试获取tracker信息
                tracker_url = None
                logger.info(f"[{service_info.name}] 检查tracker属性:")
                
                if hasattr(torrent, 'tracker'):
                    logger.info(f"[{service_info.name}] torrent.tracker存在: {getattr(torrent, 'tracker', None)}")
                    if torrent.tracker:
                        tracker_url = torrent.tracker
                
                if hasattr(torrent, 'trackers'):
                    logger.info(f"[{service_info.name}] torrent.trackers存在: {getattr(torrent, 'trackers', None)}")
                    logger.info(f"[{service_info.name}] torrent.trackers类型: {type(getattr(torrent, 'trackers', None))}")
                    if torrent.trackers:
                        # 处理TrackersList对象
                        if hasattr(torrent.trackers, '__iter__'):
                            # 遍历tracker对象，找到有效的tracker URL
                            for tracker_obj in torrent.trackers:
                                if hasattr(tracker_obj, 'url') and tracker_obj.url:
                                    # 跳过DHT、PeX、LSD等特殊tracker
                                    if not any(skip in tracker_obj.url for skip in ['[DHT]', '[PeX]', '[LSD]']):
                                        tracker_url = tracker_obj.url
                                        logger.info(f"[{service_info.name}] 找到有效tracker: {tracker_url}")
                                        break
                        # 如果有多个tracker，使用第一个
                        elif isinstance(torrent.trackers, list) and len(torrent.trackers) > 0:
                            tracker_url = torrent.trackers[0]
                            logger.info(f"[{service_info.name}] 使用第一个tracker: {tracker_url}")
                        elif isinstance(torrent.trackers, str):
                            tracker_url = torrent.trackers
                            logger.info(f"[{service_info.name}] 使用字符串tracker: {tracker_url}")
                        else:
                            logger.info(f"[{service_info.name}] trackers格式不支持: {torrent.trackers}")
                
                logger.info(f"[{service_info.name}] 最终tracker_url: {tracker_url}")
                
                if tracker_url:
                    # 使用新的站点名称获取逻辑
                    site_name = self._get_site_name_from_tracker(tracker_url)
                    logger.info(f"[{service_info.name}] 解析tracker: {tracker_url} -> {site_name}")
                    tracker_info[site_name] = 1
                    volume_info[site_name] = torrent_size
                else:
                    # 没有tracker信息，使用默认站点名称
                    site_name = '其他站点'
                    logger.info(f"[{service_info.name}] 未找到tracker信息，使用默认站点名")
                    tracker_info[site_name] = 1
                    volume_info[site_name] = torrent_size

            # 导出种子文件
            torrent_file = self._export_qb_torrent_via_api(torrent_hash, service_info)
            if not torrent_file:
                logger.error(f"[{service_info.name}] 导出种子文件失败: {torrent_name}")
                return False, tracker_info, volume_info

            # 删除原任务
            if not service_info.instance.delete_torrents(ids=torrent_hash, delete_file=False):
                logger.error(f"[{service_info.name}] 删除原任务失败: {torrent_name}")
                return False, tracker_info, volume_info

            # 重新添加任务
            try:
                # 读取种子文件内容
                with open(torrent_file, 'rb') as f:
                    content = f.read()
                
                # 构建添加参数
                add_params = {
                    "download_dir": save_path,
                    "is_paused": not self._autostart,
                    "tag": [self._processedtag],
                    "is_skip_checking": True  # 默认跳过校验
                }
                
                # 添加分类
                if self._processedcategory:
                    # 如果配置了处理完成后加分类，使用新分类
                    add_params["category"] = self._processedcategory
                elif self._remain_category and category:
                    # 如果配置了保留原分类，使用原分类
                    add_params["category"] = category

                logger.info(f"[{service_info.name}] 添加任务参数: {add_params}")
                
                result = service_info.instance.add_torrent(content=content, **add_params)
                if not result:
                    logger.error(f"[{service_info.name}] 重新添加任务失败: {torrent_name}")
                    return False, tracker_info, volume_info
            except Exception as e:
                logger.error(f"[{service_info.name}] 重新添加任务异常: {torrent_name}, 错误: {e}")
                return False, tracker_info, volume_info

            logger.info(f"[{service_info.name}] 跳校成功: {torrent_name}")
            return True, tracker_info, volume_info

        except Exception as e:
            logger.error(f"[{service_info.name}] 跳校失败: {e}")
            return False, {}, {}

    def _export_qb_torrent_via_api(self, torrent_hash: str, service_info: ServiceInfo) -> Optional[str]:
        """通过API导出种子文件"""
        try:
            service = service_info.instance
            
            # 获取动态主机和端口
            host = getattr(service, '_host', 'localhost')
            port = getattr(service, '_port', 8080)
            
            # 构建基础URL
            if host.startswith(('http://', 'https://')):
                base_url = f"{host}:{port}"
            else:
                base_url = f"http://{host}:{port}"
            
            # 导出种子
            export_url = f"{base_url}/api/v2/torrents/export"
            params = {"hash": torrent_hash}
            
            logger.info(f"[{service_info.name}] 导出种子: {export_url}")
            
            # 使用RequestUtils发送请求
            response = RequestUtils(timeout=20).get_res(export_url, params=params)
            
            if response and response.status_code == 200:
                # 保存种子文件
                import tempfile
                data_dir = tempfile.gettempdir()
                torrent_file = os.path.join(data_dir, f"{torrent_hash}.torrent")
                with open(torrent_file, 'wb') as f:
                    f.write(response.content)
                
                logger.info(f"[{service_info.name}] 种子文件导出成功: {torrent_file}")
                return torrent_file
            else:
                logger.error(f"[{service_info.name}] 导出种子失败，状态码: {response.status_code if response else 'None'}")
                return None
                
        except Exception as e:
            logger.error(f"[{service_info.name}] 导出种子异常: {e}")
            return None

    def stop_service(self):
        """停止插件"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
<<<<<<< HEAD
            print(str(e))
=======
            print(str(e))
>>>>>>> c9546f4aab7064eb4548585b4bb98c6136929d5a
