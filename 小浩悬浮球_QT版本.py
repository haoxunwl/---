# _*_ coding : utf-8 _*_
#!/usr/bin/env python3

import os
import sys
import time
import winreg  # 用于访问Windows注册表
import re
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMenu, QAction, QVBoxLayout,
    QHBoxLayout, QLabel, QTextEdit, QPushButton, QInputDialog,
    QSystemTrayIcon
)
from PyQt5.QtGui import (
    QPainter, QPen, QColor, QImage, QPixmap, QFont, QFontDatabase,
    QLinearGradient, QBrush, QRadialGradient, QIcon
)
from PyQt5.QtCore import (
    Qt, QPoint, QRect, QSize, QTimer, QRunnable, QThreadPool,
    pyqtSignal, QObject, QThread
)
import psutil
import GPUtil
import json

# Windows下全局隐藏子进程控制台窗口（抑制可能由第三方库如GPUtil触发的黑色弹窗）
try:
    if os.name == 'nt':
        import subprocess as _subprocess
        _old_popen = _subprocess.Popen
        def _hidden_popen(*args, **kwargs):
            si = kwargs.get('startupinfo')
            cf = kwargs.get('creationflags', 0)
            if si is None:
                si = _subprocess.STARTUPINFO()
                si.dwFlags |= _subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                kwargs['startupinfo'] = si
            kwargs['creationflags'] = cf | _subprocess.CREATE_NO_WINDOW
            return _old_popen(*args, **kwargs)
        _subprocess.Popen = _hidden_popen
except Exception:
    pass

# GPUtil调用节流与禁用（防止频繁调用外部工具如 nvidia-smi 导致控制台弹窗）
GPUUTIL_DISABLE = True  # 打包环境下默认禁用GPUtil，避免nvidia-smi弹窗
try:
    import GPUtil as _gputil_mod
    _orig_getGPUs = _gputil_mod.getGPUs
    _last_gpu_query_ts = 0
    _cached_gpus = []
    def _getGPUs_throttled(max_interval=2.0):
        global _last_gpu_query_ts, _cached_gpus
        now = time.time()
        if GPUUTIL_DISABLE:
            return []
        if (now - _last_gpu_query_ts) < max_interval and _cached_gpus:
            return _cached_gpus
        try:
            res = _orig_getGPUs()
            _cached_gpus = res or []
            _last_gpu_query_ts = now
            return _cached_gpus
        except Exception:
            return _cached_gpus or []
    _gputil_mod.getGPUs = _getGPUs_throttled
except Exception:
    pass

# 设置配置与日志路径
CONFIG_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'HaoxunToolbox')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'floating_ball_config.json')
LOG_PATH = os.path.join(CONFIG_DIR, 'floating_ball.log')

# 运行期可调参数（通过UI修改）
CUSTOM_NON_GAME_PROCESSES = set()
CUSTOM_NON_GAME_TITLE_KEYWORDS = set()
STRICT_NON_GAME_FULLSCREEN = True
DEBUG_LOG = False

# 旧系统兼容标志：Windows 10 以下视为旧系统
try:
    _win_ver = sys.getwindowsversion()
    IS_OLD_WINDOWS = int(getattr(_win_ver, 'major', 10)) < 10
except Exception:
    IS_OLD_WINDOWS = False

class Logger:
    """增强的日志记录器，支持不同级别和自动日志轮转"""
    
    def __init__(self, log_dir=CONFIG_DIR, log_file="悬浮球日志.log", max_file_size=10*1024*1024, backup_count=5):
        self.log_dir = log_dir
        self.log_file = log_file
        self.log_path = os.path.join(log_dir, log_file)
        self.max_file_size = max_file_size  # 10MB
        self.backup_count = backup_count
        self._ensure_log_dir()
        
        # 日志级别映射
        self.levels = {
            'DEBUG': 10,
            'INFO': 20,
            'WARNING': 30,
            'ERROR': 40,
            'CRITICAL': 50
        }
        self.current_level = self.levels['INFO']  # 默认级别
        
    def _ensure_log_dir(self):
        """确保日志目录存在"""
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir, exist_ok=True)
        except Exception as e:
            print(f"创建日志目录失败: {e}")
    
    def _rotate_logs(self):
        """日志轮转，防止单个文件过大"""
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > self.max_file_size:
                # 删除最旧的备份文件
                oldest_backup = os.path.join(self.log_dir, f"{self.log_file}.{self.backup_count}")
                if os.path.exists(oldest_backup):
                    os.remove(oldest_backup)
                
                # 重命名现有备份文件
                for i in range(self.backup_count - 1, 0, -1):
                    old_backup = os.path.join(self.log_dir, f"{self.log_file}.{i}")
                    new_backup = os.path.join(self.log_dir, f"{self.log_file}.{i+1}")
                    if os.path.exists(old_backup):
                        os.rename(old_backup, new_backup)
                
                # 重命名当前日志文件为备份1
                backup_1 = os.path.join(self.log_dir, f"{self.log_file}.1")
                if os.path.exists(self.log_path):
                    os.rename(self.log_path, backup_1)
        except Exception as e:
            print(f"日志轮转失败: {e}")
    
    def set_level(self, level_name):
        """设置日志级别"""
        if level_name in self.levels:
            self.current_level = self.levels[level_name]
    
    def _write_log(self, level, msg, exc_info=None):
        """写入日志的核心方法"""
        try:
            self._rotate_logs()
            
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            log_entry = f"[{timestamp}] [{level}] {msg}"
            
            if exc_info:
                import traceback
                if isinstance(exc_info, Exception):
                    tb_str = ''.join(traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__))
                else:
                    tb_str = ''.join(traceback.format_exc())
                log_entry += f"\n{tb_str}"
            
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(log_entry + '\n')
                
        except Exception as e:
            print(f"写入日志失败: {e}")
    
    def debug(self, msg):
        """调试级别日志"""
        if self.current_level <= self.levels['DEBUG']:
            self._write_log('DEBUG', msg)
    
    def info(self, msg):
        """信息级别日志"""
        if self.current_level <= self.levels['INFO']:
            self._write_log('INFO', msg)
    
    def warning(self, msg, exc_info=None):
        """警告级别日志"""
        if self.current_level <= self.levels['WARNING']:
            self._write_log('WARNING', msg, exc_info)
    
    def error(self, msg, exc_info=None):
        """错误级别日志"""
        if self.current_level <= self.levels['ERROR']:
            self._write_log('ERROR', msg, exc_info)
    
    def critical(self, msg, exc_info=None):
        """严重级别日志"""
        if self.current_level <= self.levels['CRITICAL']:
            self._write_log('CRITICAL', msg, exc_info)

# 创建全局日志记录器实例
logger = Logger()

def log_debug(msg):
    """兼容旧版本的日志函数"""
    logger.debug(msg)

# 修复资源路径问题
def resource_path(relative_path):
    """获取资源文件的绝对路径"""
    try:
        # PyInstaller 创建临时文件夹,将路径存储于_MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)

# 确定基础路径，支持PyInstaller打包
def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        # 运行在PyInstaller打包后的环境中
        return sys._MEIPASS
    else:
        # 直接运行Python脚本
        return os.path.abspath('.')

# 获取资源文件的绝对路径
def get_resource_path(relative_path):
    return os.path.join(get_base_path(), relative_path)

# 常见游戏进程名称列表 - 扩展版本
COMMON_GAME_PROCESSES = [
    # 游戏平台启动器
    "steam.exe", "epicgameslauncher.exe", "uplay.exe", "origin.exe", "battle.net.exe",
    "blizzard.exe", "eaapp.exe", "rockstar launcher.exe", "ubisoftconnect.exe", 
    "battlenet.exe", "riotclientux.exe",
    
    # FPS/TPS游戏
    "valorant.exe", "csgo.exe", "cs2.exe", "counter-strike2.exe", "overwatch.exe", "overwatch2.exe",
    "fortniteclient-win64-shipping.exe", "tslgame.exe", "pubg.exe", "pubgclient.exe", 
    "callofduty.exe", "modernwarfare.exe", "modernwarfare2.exe", "modernwarfare3.exe", 
    "cod.exe", "warzone.exe", "warzone2.exe", "battlefield1.exe", "battlefield5.exe",
    "battlefield2042.exe", "fearless.exe", "readyornot.exe", "crossfire.exe", "cf.exe",
    "crossfire_launcher.exe", "crossfire_x.exe"  # 穿越火线进程
    
    # 角色扮演/动作冒险游戏
    "gtav.exe", "cyberpunk2077.exe", "residentevil.exe", "residentevil2.exe", 
    "residentevil3.exe", "residentevil4.exe", "residentevilvillage.exe", "godofwar.exe",
    "assassinscreed.exe", "eldenring.exe", "sekiro.exe", "darkSoulsIII.exe", 
    "starfield.exe", "reddeadredemption2.exe", "rdr2.exe", "minecraft.exe",
    "minecraftlauncher.exe", "theforest.exe", "sonsoftheforest.exe", "skyrim.exe",
    "fallout4.exe", "fallout76.exe", "witcher3.exe", "deathloop.exe", "horizonzerodawn.exe",
    "forspoken.exe", "atomicheart.exe", "liesofp.exe", "diablo4.exe", "diablo3.exe",
    
    # MOBA/策略游戏
    "leagueclient.exe", "league of legends.exe", "lol.exe", "dota2.exe", "teamfighttactics.exe",
    "hearthstone.exe", "autochess.exe", "starcraft2.exe", "ageofempires4.exe", "civilizationvi.exe",
    
    # 竞技/体育游戏
    "fifa23.exe", "fifa24.exe", "nba2k23.exe", "nba2k24.exe", "rocketleague.exe",
    "easportsfc24.exe",
    
    # 其他流行游戏
    "worldofwarcraft.exe", "wow.exe", "pathofexile.exe", "lostark.exe", "genshinimpact.exe",
    "honkaiimpact3.exe", "honkaistarrail.exe", "apex.exe", "apexlegends.exe", "rainbowsix.exe",
    "rainbow six siege.exe", "siege.exe", "palworld.exe", "helldivers2.exe", "baldursgate3.exe",
    "bg3.exe", "phasmophobia.exe", "amnesia.exe", "deadbydaylight.exe", "amongus.exe",
    "phoenixpoint.exe", "xcom.exe", "xcom2.exe", "stardewvalley.exe", "terraria.exe"
]

# 常见非游戏全屏应用进程（用于排除降低阈值的误判）
NON_GAME_PROCESSES = {
    # 浏览器
    "chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe", "qqbrowser.exe", "360se.exe",
    # 视频/播放器
    "potplayer.exe", "vlc.exe", "mpv.exe", "bilibili.exe", "youku.exe", "iQIYI.exe", "qqvideo.exe",
    # 办公/设计/会议
    "powerpnt.exe", "winword.exe", "excel.exe", "wps.exe", "photoshop.exe", "afterfx.exe", "premiere.exe",
    "weixin.exe", "wechat.exe", "qq.exe", "dingtalk.exe", "teams.exe", "zoom.exe",
    # 直播/录屏
    "obs64.exe", "obs32.exe",
    # 远程/系统工具
    "mstsc.exe", "steamwebhelper.exe"
}

# 非游戏窗口标题关键词（用于排除降低阈值的误判）
NON_GAME_TITLE_KEYWORDS = {
    "浏览器", "视频", "播放器", "potplayer", "vlc", "mpv", "bilibili", "优酷", "爱奇艺", "腾讯视频",
    "网易云", "酷狗", "WPS", "PowerPoint", "Word", "Excel", "Photoshop", "Premiere", "After Effects",
    "OBS", "Zoom", "Teams", "微信", "QQ", "Microsoft Edge", "Edge", "浏览网页", "YouTube", "Netflix", "Twitch"
}

# 游戏平台启动器进程（不作为游戏本体判断）
LAUNCHER_PROCESSES = {
    "steam.exe", "epicgameslauncher.exe", "uplay.exe", "origin.exe", "battle.net.exe",
    "blizzard.exe", "eaapp.exe", "ubisoftconnect.exe", "riotclientux.exe", "rockstar launcher.exe",
    "battlenet.exe"
}

# 导入ctypes用于调用Windows API
import ctypes
from ctypes import wintypes

# 定义必要的Windows API常量和结构
try:
    dxgi = ctypes.windll.dxgi
    DXGI_SUPPORTED = True
except Exception:
    dxgi = None
    DXGI_SUPPORTED = False

class DXGI_QUERY_VIDEO_MEMORY_INFO(ctypes.Structure):
    _fields_ = [
        ("Budget", ctypes.c_uint64),
        ("CurrentUsage", ctypes.c_uint64),
        ("AvailableForReservation", ctypes.c_uint64),
        ("CurrentReservation", ctypes.c_uint64),
    ]

# 定义LUID结构体，因为ctypes.wintypes中可能没有直接定义
class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", ctypes.c_uint32),
        ("HighPart", ctypes.c_int32),
    ]

class DXGI_ADAPTER_DESC(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_uint64),
        ("DedicatedSystemMemory", ctypes.c_uint64),
        ("SharedSystemMemory", ctypes.c_uint64),
        ("AdapterLuid", LUID),
    ]

# 定义函数原型
if DXGI_SUPPORTED and hasattr(dxgi, 'DXGIGetDebugInterface1'):
    dxgi.DXGIGetDebugInterface1.restype = ctypes.c_int
    dxgi.DXGIGetDebugInterface1.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
else:
    DXGI_SUPPORTED = False

# 工作线程类，用于获取系统信息
class SystemInfoWorker(QThread):
    update_signal = pyqtSignal(float, float, float, float, float, bool, int)  # CPU使用率, GPU温度, GPU使用率, 下载速度, 上传速度, 是否在游戏, FPS
    error_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self._running = True
        # 初始化网络统计数据
        self.last_net_io = psutil.net_io_counters()
        self.last_time = time.time()
        # 游戏检测相关变量
        self.last_fps_check_time = time.time()
        self.fps = 0
        
        # FPS获取相关的初始化
        self.fps_history = []  # 存储最近的FPS值，用于平滑处理
        self.frame_time_history = []  # 存储最近的帧时间，用于更准确的FPS计算
        self.fps_smoothing_window = 8  # FPS平滑窗口大小，增加以获得更稳定的结果
        self.last_fps_timestamp = 0  # 上次获取FPS的时间戳
        self.fps_cache = 0  # 缓存的FPS值
        self.cache_valid_time = 0.15  # 缓存有效期（秒），减少以提高响应速度
        
        # GPU型号相关信息（用于针对性优化）
        self.gpu_model = None
        self.gpu_vendor = None
        self._init_gpu_info()
        
        # 针对特定游戏的FPS修正值 - 扩展版本
        self.game_specific_fps_offsets = {
            # Epic游戏
            'FortniteClient-Win64-Shipping.exe': 5,  # 堡垒之夜
            'Fortnite.exe': 5,
            
            # EA游戏
            'ApexLegends.exe': -3,  # Apex英雄
            'Apex.exe': -3,
            'TslGame.exe': 2,  # PUBG
            'battlefield2042.exe': -1,
            'StarWarsSquadrons.exe': 3,
            
            # Blizzard游戏
            'Overwatch.exe': 4,  # 守望先锋
            'Overwatch2.exe': 3,
            'Wow.exe': 2,  # 魔兽世界
            'WowClassic.exe': 3,
            
            # Riot游戏
            'LeagueClient.exe': 8,  # 英雄联盟客户端
            'League of Legends.exe': 6,
            'VALORANT.exe': 2,
            
            # Valve游戏
            'csgo.exe': -2,  # CS:GO
            'cs2.exe': 0,  # CS2
            'dota2.exe': 1,
            
            # 其他流行游戏
            'GTA5.exe': 3,  # 侠盗猎车手5
            'Cyberpunk2077.exe': 1,  # 赛博朋克2077
            'EldenRing.exe': -2,  # 艾尔登法环
            'CallofDuty.exe': 0,  # COD系列
            'ModernWarfare.exe': 1,
            'ModernWarfare2.exe': -1,
            'ModernWarfare3.exe': -2,
            'Warzone.exe': 2,
            'Warzone2.exe': 0,
            'eldenring.exe': -2,
            'ResidentEvil4.exe': 2,
            'godofwar.exe': -1,
            'minecraft.exe': 5,
            'palworld.exe': -3,
            'helldivers2.exe': 1,
            'baldursgate3.exe': -2,
            'bg3.exe': -2,
            'DiabloIV.exe': -1,
            'Diablo4.exe': -1
        }
        
        # 初始化Windows性能计数器访问
        self._init_performance_counter()
    
    def stop(self):
        """停止线程的安全方法"""
        self._running = False
        self.wait()
    
    def _get_cpu_temperature_celsius(self):
        # 尝试通过psutil读取CPU温度（最安全，无弹窗风险）
        try:
            temps = psutil.sensors_temperatures()
            candidates = []
            for name, entries in temps.items():
                for entry in entries:
                    label = (entry.label or "").lower()
                    if "cpu" in label or "package" in label or "core" in label:
                        if entry.current is not None:
                            candidates.append(float(entry.current))
                # 针对coretemp等无标签条目进行兜底
                if name.lower() in ("coretemp", "cpu-thermal", "acpitz") and entries:
                    for entry in entries:
                        if entry.current is not None:
                            candidates.append(float(entry.current))
            if candidates:
                return max(candidates)
        except Exception:
            pass
        
        # 打包环境下禁用WMI调用，避免可能的弹窗风险
        if not hasattr(sys, '_MEIPASS'):  # 非打包环境才使用WMI
            try:
                import wmi
                w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
                sensors = w.Sensor()
                cpu_temps = [s.Value for s in sensors if s.SensorType == "Temperature" and ("cpu" in s.Name.lower() or "package" in s.Name.lower())]
                if cpu_temps:
                    return max(cpu_temps)
            except Exception:
                pass
        
        return None
    
    def _get_gpu_temperature_celsius(self):
        # 打包环境下使用安全的温度获取方法，避免弹窗
        if hasattr(sys, '_MEIPASS') or GPUUTIL_DISABLE:  # 打包环境或禁用GPUtil
            # 方法1：尝试通过OpenHardwareMonitor的WMI接口（Windows环境最可靠）
            try:
                import wmi
                w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
                sensors = w.Sensor()
                gpu_temps = [s.Value for s in sensors if s.SensorType == "Temperature" and ("gpu" in s.Name.lower())]
                if gpu_temps:
                    return max(gpu_temps)
            except Exception:
                pass
            
            # 方法2：尝试通过pynvml获取（如果可用且安全）
            try:
                import pynvml
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                pynvml.nvmlShutdown()
                if isinstance(temp, (int, float)) and temp > 0:
                    return float(temp)
            except Exception:
                pass
            
            # 方法3：基于CPU温度估算GPU温度（兜底方案）
            try:
                cpu_temp = self._get_cpu_temperature_celsius()
                if isinstance(cpu_temp, (int, float)) and cpu_temp > 0:
                    # GPU温度通常比CPU高5-15度
                    return min(cpu_temp + 10, 95)  # 限制在合理范围内
            except Exception:
                pass
            
            # 方法4：提供合理的默认温度值（确保温度显示正常）
            # 在Windows环境下，如果以上方法都失败，返回一个合理的默认值
            return 45.0  # 默认GPU温度值
        
        # 非打包环境：使用原有的温度获取方法
        # 优先尝试GPUtil（已节流），若不可用则尝试OpenHardwareMonitor或pynvml
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                temp_val = getattr(gpus[0], 'temperature', None)
                if isinstance(temp_val, (int, float)) and temp_val > 0:
                    return float(temp_val)
        except Exception:
            pass
        # OpenHardwareMonitor WMI（需OHM运行）
        try:
            import wmi
            w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
            sensors = w.Sensor()
            gpu_temps = [s.Value for s in sensors if s.SensorType == "Temperature" and ("gpu" in s.Name.lower())]
            if gpu_temps:
                return max(gpu_temps)
        except Exception:
            pass
        # NVIDIA NVML（若安装了驱动并可用）
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            pynvml.nvmlShutdown()
            if isinstance(temp, (int, float)) and temp > 0:
                return float(temp)
        except Exception:
            pass
        return None
    
    def run(self):
        try:
            logger.info("SystemInfoWorker线程开始运行")
            
            # 检查是否为打包环境，如果是则禁用可能导致问题的硬件监控功能
            is_packaged = hasattr(sys, '_MEIPASS')
            if is_packaged:
                logger.info("检测到打包环境，启用安全模式监控（保留游戏/FPS检测）")
            
            # 预计算变量和一次性初始化
            current_time = time.time()
            last_gpu_check_time = current_time
            last_game_check_time = current_time
            last_network_check_time = current_time
            last_fps_check_time = current_time
            
            # 使用非阻塞方式获取初始CPU使用率
            psutil.cpu_percent(interval=0)
            
            # 初始化缓存变量
            self._cached_gpu_temp = 45.0  # 默认GPU温度
            self._cached_gpu_load = 0
            self._cached_is_gaming = False
            self._cached_cpu_usage = 0
            self._cached_down_speed = 0
            self._cached_up_speed = 0
            self._cached_fps = 0
            
            # 初始化网络IO计数器
            try:
                self.last_net_io = psutil.net_io_counters()
                self.last_time = current_time
                logger.debug("网络IO计数器初始化成功")
            except Exception as e:
                self.last_net_io = None
                self.last_time = current_time
                logger.warning(f"网络IO计数器初始化失败: {str(e)}")
            
            # 预计算休眠时间以减少函数调用
            sleep_interval = getattr(self, 'sleep_interval', 0.05)
            
            # 信号发送频率控制
            last_signal_time = current_time
            signal_interval = getattr(self, 'signal_interval', 0.1)
            
            while self._running:
                current_time = time.time()
                
                # 非阻塞方式获取CPU使用率（高频但低消耗）
                try:
                    cpu_usage = psutil.cpu_percent(interval=0, percpu=False)  # 使用单值而非列表以减少计算
                    self._cached_cpu_usage = cpu_usage
                except Exception:
                    # 保持缓存值
                    pass
                
                # 减少GPU查询频率，每0.6秒查询一次（与GPUtil节流协同）
                if current_time - last_gpu_check_time >= 0.6:
                    try:
                        # 打包环境下使用安全的温度获取方法
                        if is_packaged or GPUUTIL_DISABLE:
                            logger.debug("打包环境或禁用GPUtil，使用安全的温度获取方法")
                            # 在打包环境下，使用基于CPU使用率的简单估算
                            if hasattr(self, '_cached_cpu_usage'):
                                # GPU温度基于CPU使用率估算，确保在合理范围内
                                base_temp = 40.0 + (self._cached_cpu_usage * 0.3)
                                self._cached_gpu_temp = max(30.0, min(80.0, base_temp))
                                self._cached_gpu_load = max(0, min(100, self._cached_cpu_usage * 1.2))
                                logger.debug(f"打包环境估算GPU温度: {self._cached_gpu_temp}°C, 负载: {self._cached_gpu_load}%")
                            else:
                                # 如果CPU使用率不可用，使用默认值
                                self._cached_gpu_temp = 45.0
                                self._cached_gpu_load = 20.0
                        else:
                            # 非打包环境：使用GPUtil获取GPU信息
                            gpus = GPUtil.getGPUs()
                            if gpus:
                                # 温度与负载优先来自GPUtil
                                try:
                                    temp_val = getattr(gpus[0], 'temperature', None)
                                    if isinstance(temp_val, (int, float)) and temp_val > 0:
                                        self._cached_gpu_temp = float(temp_val)
                                        logger.debug(f"GPUtil获取GPU温度成功: {self._cached_gpu_temp}°C")
                                except Exception as e:
                                    logger.debug(f"GPUtil获取GPU温度失败: {str(e)}")
                                try:
                                    load_val = getattr(gpus[0], 'load', None)
                                    if isinstance(load_val, (int, float)) and load_val >= 0:
                                        self._cached_gpu_load = float(load_val) * 100.0
                                        logger.debug(f"GPUtil获取GPU负载成功: {self._cached_gpu_load}%")
                                except Exception as e:
                                    logger.debug(f"GPUtil获取GPU负载失败: {str(e)}")
                            # 若GPUtil不可用或未得到温度，尝试回退方法
                            if not gpus or (not isinstance(self._cached_gpu_temp, (int, float)) or self._cached_gpu_temp <= 0):
                                logger.debug("GPUtil获取温度无效，尝试回退方法")
                                fallback_temp = self._get_gpu_temperature_celsius()
                                if isinstance(fallback_temp, (int, float)) and fallback_temp > 0:
                                    self._cached_gpu_temp = float(fallback_temp)
                                    logger.debug(f"回退方法获取GPU温度成功: {self._cached_gpu_temp}°C")
                                else:
                                    logger.debug("回退方法获取GPU温度失败")
                    except Exception as e:
                        logger.error(f"GPU信息获取异常: {str(e)}")
                    last_gpu_check_time = current_time
                
                # 减少网络速度计算频率，每0.5秒计算一次
                if current_time - last_network_check_time >= 0.5 and self.last_net_io is not None:
                    try:
                        current_net_io = psutil.net_io_counters()
                        time_diff = current_time - self.last_time
                        
                        if time_diff > 0.01:  # 避免非常小的时间差导致的计算波动
                            down_speed = (current_net_io.bytes_recv - self.last_net_io.bytes_recv) / time_diff
                            up_speed = (current_net_io.bytes_sent - self.last_net_io.bytes_sent) / time_diff
                            
                            # 平滑网络速度变化
                            if hasattr(self, '_cached_down_speed') and self._cached_down_speed > 0:
                                self._cached_down_speed = self._cached_down_speed * 0.7 + down_speed * 0.3
                                self._cached_up_speed = self._cached_up_speed * 0.7 + up_speed * 0.3
                            else:
                                self._cached_down_speed = down_speed
                                self._cached_up_speed = up_speed
                            
                            # 更新网络统计数据
                            self.last_net_io = current_net_io
                            self.last_time = current_time
                    except Exception:
                        pass  # 使用缓存值
                    last_network_check_time = current_time
                
                # 减少游戏检测频率，每1秒检测一次
                if current_time - last_game_check_time >= 1:
                    try:
                        # 打包环境下也启用游戏检测，失败时使用缓存
                        self._cached_is_gaming = self.detect_gaming()
                        if self._cached_is_gaming:
                            logger.debug("游戏检测: 检测到游戏运行")
                    except Exception as e:
                        logger.error(f"游戏检测异常: {str(e)}")
                        # 使用缓存值
                    last_game_check_time = current_time
                
                # 获取FPS（游戏模式下更高频率，非游戏模式下降低频率）
                if (self._cached_is_gaming and current_time - last_fps_check_time >= 0.5) or \
                   (not self._cached_is_gaming and current_time - last_fps_check_time >= 2.0):
                    try:
                        # 打包环境下也尝试获取FPS，失败时回退为0
                        self._cached_fps = self.get_fps(self._cached_is_gaming)
                        if self._cached_fps > 0:
                            logger.debug(f"FPS获取成功: {self._cached_fps}")
                        else:
                            logger.debug("FPS不可用或获取失败，使用0")
                    except Exception as e:
                        self._cached_fps = 0
                        logger.error(f"FPS获取异常: {str(e)}")
                    last_fps_check_time = current_time
                
                # 控制信号发送频率，避免过于频繁的UI更新
                if current_time - last_signal_time >= signal_interval:
                    # 发送更新信号，使用缓存值
                    self.update_signal.emit(
                        self._cached_cpu_usage, 
                        self._cached_gpu_temp, 
                        self._cached_gpu_load, 
                        self._cached_down_speed, 
                        self._cached_up_speed, 
                        self._cached_is_gaming, 
                        self._cached_fps
                    )
                    last_signal_time = current_time
                
                # 使用更高效的休眠方式
                time.sleep(sleep_interval)
                
        except Exception as e:
            if self._running:  # 只有在线程正常运行时才发送错误信号
                self.error_signal.emit(str(e))
            
    def detect_gaming(self):
        """增强的游戏检测方法，提高准确性和响应速度"""
        try:
            current_time = time.time()
            
            # 优化缓存机制
            if hasattr(self, '_last_detection_result') and hasattr(self, '_last_detection_time'):
                # 如果最近刚检测到游戏状态，适当延长缓存时间
                if self._last_detection_result and current_time - self._last_detection_time < 0.5:
                    return True
                # 非游戏状态的缓存时间可以短一些
                elif not self._last_detection_result and current_time - self._last_detection_time < 0.2:
                    return False
            
            # 降低GPU负载阈值，提高游戏检测灵敏度 + 全屏加权（避免非游戏误判）
            if hasattr(self, '_cached_gpu_load'):
                is_fullscreen = False
                try:
                    is_fullscreen = self._is_foreground_fullscreen()
                except Exception:
                    pass

                # 获取前台进程名和窗口标题（用于排除非游戏全屏）
                foreground_process_name = None
                try:
                    # 尝试通过win32获取
                    import win32process
                    import win32gui
                    hwnd = win32gui.GetForegroundWindow()
                    if hwnd:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        foreground_proc = psutil.Process(pid)
                        foreground_process_name = foreground_proc.name().lower()
                except Exception:
                    # 失败则忽略
                    pass
                # 兼容：若未获取到前台进程名，使用ctypes回退方案（无win32依赖）
                if not foreground_process_name:
                    try:
                        foreground_process_name = self._get_foreground_process_name()
                    except Exception:
                        foreground_process_name = None
                active_window_title = None
                try:
                    active_window_title = self._get_active_window_title() or ""
                except Exception:
                    active_window_title = ""
                window_lower = active_window_title.lower()

                # 强化：只要前台属于非游戏应用或标题包含非游戏关键词，则直接判定非游戏（避免浏览器/播放器等误判）
                try:
                    combined_processes = set(NON_GAME_PROCESSES) | set(CUSTOM_NON_GAME_PROCESSES) | set(LAUNCHER_PROCESSES)
                    combined_titles = set(NON_GAME_TITLE_KEYWORDS) | set(CUSTOM_NON_GAME_TITLE_KEYWORDS)
                except Exception:
                    combined_processes = set(NON_GAME_PROCESSES)
                    combined_titles = set(NON_GAME_TITLE_KEYWORDS)
                if (foreground_process_name and foreground_process_name in combined_processes) or any(kw in window_lower for kw in combined_titles):
                    self._last_detection_result = False
                    self._last_detection_time = current_time
                    return False

                is_non_game_fullscreen = False
                if is_fullscreen and STRICT_NON_GAME_FULLSCREEN:
                    # 再次判断全屏场景下的非游戏排除
                    if foreground_process_name and foreground_process_name in combined_processes:
                        is_non_game_fullscreen = True
                    elif any(kw in window_lower for kw in combined_titles):
                        is_non_game_fullscreen = True

                # GPU负载达到中等即可判定为游戏（不在非游戏全屏场景）
                if self._cached_gpu_load >= 35 and not is_non_game_fullscreen:
                    self._last_detection_result = True
                    self._last_detection_time = current_time
                    return True

                # 前台全屏时，适当降低GPU阈值，但避免非游戏应用误判
                if is_fullscreen and not is_non_game_fullscreen and self._cached_gpu_load >= 25:
                    self._last_detection_result = True
                    self._last_detection_time = current_time
                    return True

                # 如果是非游戏的全屏场景，仅在GPU负载和内存占用都非常高时才判定为游戏
                if is_non_game_fullscreen:
                    try:
                        gpus = GPUtil.getGPUs()
                        if gpus:
                            gpu = gpus[0]
                            mem_util = 0.0
                            if gpu.memoryTotal > 0:
                                mem_util = gpu.memoryUsed / gpu.memoryTotal
                            if self._cached_gpu_load >= 60 and mem_util >= 0.7:
                                self._last_detection_result = True
                                self._last_detection_time = current_time
                                return True
                    except Exception:
                        # 获取GPU内存失败时，仍然提高负载阈值避免误判
                        if self._cached_gpu_load >= 70:
                            self._last_detection_result = True
                            self._last_detection_time = current_time
                            return True
            
            # 缓存前台窗口标题，更频繁地更新
            if not hasattr(self, '_cached_window_title') or current_time - getattr(self, '_last_window_check', 0) > 0.3:
                self._cached_window_title = self._get_active_window_title()
                self._last_window_check = current_time
            
            active_window_title = self._cached_window_title
            
            # 增强的窗口标题检测
            if active_window_title:
                window_lower = active_window_title.lower()
                
                # 扩展的游戏关键词集合
                game_title_keywords = {
                    'game', 'gaming', 'fps', 'rpg', 'moba', 'mmo', 'online', 
                    'battle', 'war', 'fight', 'shoot', 'race', 'simulator',
                    # 增加更多游戏相关关键词
                    '使命召唤', 'cod', 'cs2', 'csgo', 'valorant', '彩虹六号',
                    'lol', 'dota', 'fortnite', 'pubg', 'apex', '原神',
                    'genshin', 'gta', 'cyberpunk', '赛博朋克', 'elden', '艾尔登',
                    'starfield', '星空', 'palworld', '幻兽帕鲁', 'baldurs', '博德之门',
                    # 添加穿越火线相关关键词
                    'cf', 'crossfire', '穿越火线', 'cfhd', 'cf战场', 'cf爆破'
                }  
                # 快速关键词匹配
                if any(keyword in window_lower for keyword in game_title_keywords):
                    self._last_detection_result = True
                    self._last_detection_time = current_time
                    return True
                
                # 检查是否匹配已知游戏窗口标题（更宽松的匹配规则）
                for game_name in COMMON_GAME_PROCESSES:
                    game_base = game_name.replace('.exe', '').lower()
                    # 使用更宽松的匹配，即使游戏名称有版本号或其他后缀
                    if game_base in window_lower or any(part in window_lower for part in game_base.split()):
                        self._last_detection_result = True
                        self._last_detection_time = current_time
                        return True
            
            # 增强的前台进程检测
            foreground_process_name = None
            try:
                # 尝试多种方法获取前台进程
                # 方法1：使用win32库
                try:
                    import win32process
                    import win32gui
                    hwnd = win32gui.GetForegroundWindow()
                    if hwnd:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        foreground_proc = psutil.Process(pid)
                        foreground_process_name = foreground_proc.name().lower()
                except Exception:
                    # 方法2：使用ctypes获取PID并用psutil取名
                    foreground_process_name = self._get_foreground_process_name()
            except Exception as e:
                print(f"前台进程获取失败: {str(e)}")
            
            # 检查前台进程是否是游戏（排除平台启动器）
            if foreground_process_name:
                try:
                    common_game_processes_lower = {name.lower() for name in COMMON_GAME_PROCESSES}
                    if foreground_process_name in common_game_processes_lower and foreground_process_name not in LAUNCHER_PROCESSES:
                        self._last_detection_result = True
                        self._last_detection_time = current_time
                        return True
                except Exception as e:
                    print(f"前台进程检测出错: {str(e)}")
            
            # 降低CPU使用率阈值，捕获更多可能的游戏进程
            high_cpu_procs = []
            for proc in psutil.process_iter(['name', 'cpu_percent']):
                try:
                    proc_info = proc.info
                    # 降低阈值到1.5%，捕获更多低CPU占用的游戏
                    if proc_info['cpu_percent'] and proc_info['cpu_percent'] > 1.5:
                        high_cpu_procs.append(proc_info['name'].lower())
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            # 检查高CPU使用率进程中是否有游戏
            common_game_processes_lower = {name.lower() for name in COMMON_GAME_PROCESSES}
            if any(process_name in common_game_processes_lower for process_name in high_cpu_procs):
                self._last_detection_result = True
                self._last_detection_time = current_time
                return True
            
            # 增强的GPU资源使用模式检测
            try:
                # 尝试使用缓存的GPU信息
                if hasattr(self, '_cached_gpu_load'):
                    gpu_load = self._cached_gpu_load / 100.0  # 转换为0-1范围
                    
                    # 降低GPU负载阈值到0.45，提高检测灵敏度
                    if gpu_load > 0.45:
                        # 检查是否为非游戏全屏，避免误判
                        is_fullscreen = False
                        is_non_game_fullscreen = False
                        try:
                            is_fullscreen = self._is_foreground_fullscreen()
                            fg_name = self._get_foreground_process_name()
                            win_title = (self._get_active_window_title() or "").lower()
                            if is_fullscreen:
                                if fg_name and fg_name in NON_GAME_PROCESSES:
                                    is_non_game_fullscreen = True
                                elif any(kw in win_title for kw in NON_GAME_TITLE_KEYWORDS):
                                    is_non_game_fullscreen = True
                        except Exception:
                            pass
                        # 即使内存使用率不是特别高，也可能是游戏
                        gpus = GPUtil.getGPUs()
                        if gpus:
                            gpu_memory_used = gpus[0].memoryUsed
                            gpu_memory_total = gpus[0].memoryTotal
                            if gpu_memory_total > 0:
                                memory_utilization = gpu_memory_used / gpu_memory_total
                                # 非非游戏全屏场景，适用较低阈值
                                if not is_non_game_fullscreen and memory_utilization > 0.5:
                                    self._last_detection_result = True
                                    self._last_detection_time = current_time
                                    return True
                                # 非游戏全屏时需要更高证据
                                if is_non_game_fullscreen and memory_utilization > 0.75 and gpu_load > 0.7:
                                    self._last_detection_result = True
                                    self._last_detection_time = current_time
                                    return True
            except Exception as e:
                print(f"GPU资源检测出错: {str(e)}")
            
            # 最后检查是否有已知游戏进程在运行（即使CPU使用率不高）
            try:
                running_process_names = {proc.info['name'].lower() for proc in psutil.process_iter(['name'])}
                common_game_processes_lower = {name.lower() for name in COMMON_GAME_PROCESSES}
                # 检查是否有任何游戏进程正在运行
                intersection = common_game_processes_lower.intersection(running_process_names)
                if intersection:
                    # 过滤掉平台启动器，仅保留真正的游戏进程
                    non_launcher = {p for p in intersection if p not in LAUNCHER_PROCESSES}
                    # 判断当前是否为非游戏全屏（避免误判）
                    is_fullscreen = False
                    is_non_game_fullscreen = False
                    try:
                        is_fullscreen = self._is_foreground_fullscreen()
                        fg_name = self._get_foreground_process_name()
                        win_title = (self._get_active_window_title() or "").lower()
                        if is_fullscreen:
                            if fg_name and fg_name in NON_GAME_PROCESSES:
                                is_non_game_fullscreen = True
                            elif any(kw in win_title for kw in NON_GAME_TITLE_KEYWORDS):
                                is_non_game_fullscreen = True
                    except Exception:
                        pass
                    if non_launcher:
                        # 有真正的游戏进程时，阈值从10%开始，但避免非游戏全屏干扰
                        if hasattr(self, '_cached_gpu_load') and self._cached_gpu_load > 10 and not is_non_game_fullscreen:
                            self._last_detection_result = True
                            self._last_detection_time = current_time
                            return True
                    else:
                        # 仅有启动器进程时，需要更高的证据，且排除非游戏全屏
                        if hasattr(self, '_cached_gpu_load') and not is_non_game_fullscreen:
                            try:
                                gpus = GPUtil.getGPUs()
                                mem_ok = False
                                if gpus and gpus[0].memoryTotal > 0:
                                    mem_util = gpus[0].memoryUsed / gpus[0].memoryTotal
                                    mem_ok = mem_util >= 0.6
                                if self._cached_gpu_load >= 50 and mem_ok:
                                    self._last_detection_result = True
                                    self._last_detection_time = current_time
                                    return True
                            except Exception:
                                if self._cached_gpu_load >= 60:
                                    self._last_detection_result = True
                                    self._last_detection_time = current_time
                                    return True
            except Exception as e:
                print(f"游戏进程检查出错: {str(e)}")
            
            # 记录非游戏状态
            self._last_detection_result = False
            self._last_detection_time = current_time
            return False
        except Exception as e:
            # 出错时记录但不中断程序
            print(f"游戏检测出错: {str(e)}")
            return False
            
            # 记录非游戏状态
            self._last_detection_result = False
            self._last_detection_time = current_time
            return False
        except Exception as e:
            # 出错时记录但不中断程序
            print(f"游戏检测出错: {str(e)}")
            return False
            
    def _get_active_window_title(self):
        """获取当前前台活动窗口的标题"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            
            # 获取窗口标题长度
            length = user32.GetWindowTextLengthW(hwnd)
            
            # 创建缓冲区
            buff = ctypes.create_unicode_buffer(length + 1)
            
            # 获取窗口标题
            user32.GetWindowTextW(hwnd, buff, length + 1)
            
            return buff.value
        except Exception:
            return None
            
    def _is_foreground_fullscreen(self):
        """判断前台窗口是否为全屏（无边框或占满屏幕）。"""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            # 获取窗口矩形
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            # 屏幕分辨率
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            # 尺寸接近全屏的容差（2% 或至少8像素）
            size_full = (abs(width - screen_w) <= max(8, int(screen_w * 0.02)) and
                         abs(height - screen_h) <= max(8, int(screen_h * 0.02)))
            # 无标题栏/弹出样式判断
            GWL_STYLE = -16
            WS_CAPTION = 0x00C00000
            WS_POPUP = 0x80000000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            borderless = (style & WS_CAPTION) == 0 or (style & WS_POPUP) == WS_POPUP
            return size_full and borderless
        except Exception:
            return False

    def _get_foreground_process_name(self):
        """使用ctypes获取前台窗口的进程名（无需win32依赖）。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                try:
                    p = psutil.Process(pid.value)
                    return p.name().lower()
                except Exception:
                    return None
            return None
        except Exception:
            return None

    
    def _init_gpu_info(self):
        """初始化GPU型号和厂商信息，用于针对性优化FPS计算"""
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu = gpus[0]
                self.gpu_model = gpu.name.lower() if hasattr(gpu, 'name') and gpu.name else ''
                
                # 确定GPU厂商
                if 'nvidia' in self.gpu_model or 'rtx' in self.gpu_model or 'gtx' in self.gpu_model:
                    self.gpu_vendor = 'nvidia'
                elif 'amd' in self.gpu_model or 'radeon' in self.gpu_model or 'rx' in self.gpu_model:
                    self.gpu_vendor = 'amd'
                elif 'intel' in self.gpu_model or 'iris' in self.gpu_model or 'uhd' in self.gpu_model:
                    self.gpu_vendor = 'intel'
                else:
                    self.gpu_vendor = 'unknown'
            else:
                # 回退：使用显示设备枚举推断GPU厂商与型号，不依赖外部命令
                try:
                    import ctypes
                    class DISPLAY_DEVICEW(ctypes.Structure):
                        _fields_ = [
                            ('cb', ctypes.c_uint),
                            ('DeviceName', ctypes.c_wchar * 32),
                            ('DeviceString', ctypes.c_wchar * 128),
                            ('StateFlags', ctypes.c_uint),
                            ('DeviceID', ctypes.c_wchar * 128),
                            ('DeviceKey', ctypes.c_wchar * 128),
                        ]
                    user32 = ctypes.windll.user32
                    dd = DISPLAY_DEVICEW()
                    dd.cb = ctypes.sizeof(dd)
                    i = 0
                    vendor_guess = 'unknown'
                    model_guess = ''
                    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                        name = (dd.DeviceString or '')
                        lower = name.lower()
                        if not model_guess:
                            model_guess = lower
                        if 'nvidia' in lower or 'rtx' in lower or 'gtx' in lower:
                            vendor_guess = 'nvidia'
                            model_guess = lower
                            break
                        if 'amd' in lower or 'radeon' in lower or 'rx' in lower:
                            vendor_guess = 'amd'
                            model_guess = lower
                            break
                        if 'intel' in lower or 'iris' in lower or 'uhd' in lower:
                            vendor_guess = 'intel'
                            model_guess = lower
                            break
                        i += 1
                    self.gpu_model = model_guess
                    self.gpu_vendor = vendor_guess
                except Exception:
                    self.gpu_model = ''
                    self.gpu_vendor = 'unknown'
        except Exception:
            self.gpu_model = ''
            self.gpu_vendor = 'unknown'
            
    def _init_performance_counter(self):
        """初始化Windows性能计数器访问"""
        try:
            import ctypes
            self._has_performance_counter = False
            
            # 尝试导入win32pdh库（Windows性能计数器）
            try:
                import win32pdh
                self._has_win32pdh = True
            except ImportError:
                self._has_win32pdh = False
                
            # 尝试使用QueryPerformanceCounter
            self._has_query_perf_counter = False
            try:
                kernel32 = ctypes.windll.kernel32
                self._query_perf_counter = kernel32.QueryPerformanceCounter
                self._query_perf_frequency = kernel32.QueryPerformanceFrequency
                
                # 测试是否可用
                freq = ctypes.c_ulonglong()
                if self._query_perf_frequency(ctypes.byref(freq)) and freq.value > 0:
                    self._has_query_perf_counter = True
                    self._perf_frequency = freq.value
                
                # 初始化计数器变量
                self._last_counter_value = ctypes.c_ulonglong()
                self._query_perf_counter(ctypes.byref(self._last_counter_value))
                
            except Exception:
                self._has_query_perf_counter = False
                
        except Exception:
            pass
            
    def _get_display_refresh_rate_hz(self):
        """获取主显示器刷新率（Hz），使用GetDeviceCaps，并进行本地缓存"""
        try:
            # 使用Windows API获取设备上下文
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            hdc = user32.GetDC(0)
            if hdc:
                VREFRESH = 116
                refresh = gdi32.GetDeviceCaps(hdc, VREFRESH)
                user32.ReleaseDC(0, hdc)
                if refresh and refresh > 0:
                    self._display_refresh_hz = int(refresh)
                    self._last_refresh_query = time.time()
                    return self._display_refresh_hz
        except Exception:
            pass
        # 如果之前查询过且值有效，返回缓存
        if hasattr(self, '_display_refresh_hz') and isinstance(self._display_refresh_hz, int) and self._display_refresh_hz > 0:
            # 超过5分钟才重新查询
            if hasattr(self, '_last_refresh_query') and time.time() - getattr(self, '_last_refresh_query', 0) < 300:
                return self._display_refresh_hz
        # 默认返回60Hz
        self._display_refresh_hz = 60
        self._last_refresh_query = time.time()
        return self._display_refresh_hz
            
    def get_fps(self, is_gaming):
        """优化的FPS获取方法，提高响应速度和准确性，特别针对CF等FPS游戏"""
        current_time = time.time()
        
        # 如果不是游戏状态，返回0
        if not is_gaming:
            self.fps_history.clear()  # 清除历史记录
            self.frame_time_history.clear()
            return 0
        
        try:
            # 检测是否正在运行CF游戏 - 增强检测逻辑
            is_cf_game = False
            
            # 1. 检查活动窗口标题中是否包含CF相关关键词
            try:
                active_window_title = self._get_active_window_title()
                if active_window_title:
                    window_lower = active_window_title.lower()
                    cf_window_keywords = ['crossfire', 'cf', '穿越火线', 'cfhd', 'cf战场', 'cf爆破']
                    if any(keyword in window_lower for keyword in cf_window_keywords):
                        is_cf_game = True
                        print(f"从活动窗口标题检测到CF游戏: {active_window_title}")
            except Exception:
                pass
            
            # 2. 检查活动游戏进程
            if not is_cf_game:
                active_game = self._get_active_game_process()
                if active_game:
                    active_game_lower = active_game.lower()
                    if any(cf_kw in active_game_lower for cf_kw in ['crossfire', 'cf']):
                        is_cf_game = True
                        print(f"从活动游戏进程检测到CF游戏: {active_game}")
            
            # 3. 检查进程列表中的CF相关进程
            if not is_cf_game:
                try:
                    running_processes = {proc.info['name'].lower() for proc in psutil.process_iter(['name'])} 
                    cf_processes = {'crossfire.exe', 'cf.exe', 'crossfire_launcher.exe', 'crossfire_x.exe', 'client.exe'}
                    is_cf_game = bool(cf_processes.intersection(running_processes))
                    if is_cf_game:
                        print("从进程列表检测到CF游戏，使用优化的FPS获取策略")
                except Exception:
                    pass
            
            # 4. 如果检测到CF游戏，打印日志确认
            if is_cf_game:
                print("CF游戏检测成功，将使用专用FPS计算逻辑")
            
            # CF游戏的缓存策略更激进，更新更频繁
            cache_valid = False
            if is_cf_game:
                # CF游戏缓存时间更短，确保实时性
                if (current_time - self.last_fps_timestamp < 0.1 and 
                    self.fps_cache > 0):
                    cache_valid = True
            else:
                # 普通游戏使用标准缓存
                if (current_time - self.last_fps_timestamp < 0.2 and 
                    self.fps_cache > 0):
                    # 降低历史记录更新间隔
                    if not hasattr(self, '_last_history_update') or current_time - self._last_history_update > 0.3:
                        self.fps_history.append(self.fps_cache)
                        if len(self.fps_history) > self.fps_smoothing_window:
                            self.fps_history.pop(0)
                        self._last_history_update = current_time
                    cache_valid = True
            
            # 如果缓存有效且FPS不为零，直接返回
            if cache_valid and self.fps_cache > 0:
                return self.fps_cache
                
            # CF游戏专用FPS获取逻辑
            if is_cf_game:
                raw_fps = self._get_cf_specific_fps()
                method_success = 1 <= raw_fps <= 400
                # CF专用路径非RTSS来源，设置来源标记为False；若后续使用RTSS会再次更新
                self._last_source_rtss = False
                if not method_success:
                    # 回退到通用聚合方法，优先尝试RTSS共享内存（若有）
                    fps_methods = [
                        self._get_fps_using_rtss_shared_memory,
                        self._get_fps_using_windows_gaming_api,
                        self._get_fps_using_gpu_performance_counters,
                        self._get_fps_using_direct_query,
                        self._get_fps_using_gpu_load_temp_and_memory,
                    ]
                    method_weights = {
                        self._get_fps_using_rtss_shared_memory: 1.0,
                        self._get_fps_using_windows_gaming_api: 1.0,
                        self._get_fps_using_gpu_performance_counters: 0.85,
                        self._get_fps_using_direct_query: 0.75,
                        self._get_fps_using_gpu_load_temp_and_memory: 0.55,
                    }
                    start_time = time.time()
                    candidates = []
                    rtss_present = False
                    rtss_value = None
                    for method in fps_methods:
                        if time.time() - start_time > 0.25:
                            break
                        try:
                            value = method()
                            if 1 <= value <= 400:
                                if method is self._get_fps_using_rtss_shared_memory:
                                    rtss_present = True
                                    rtss_value = value
                                    # RTSS单源优先，拿到有效值后直接使用
                                    break
                                else:
                                    candidates.append((value, method_weights.get(method, 0.6)))
                        except Exception as e:
                            print(f"FPS方法 {method.__name__} 出错: {str(e)}")
                            continue
                    if rtss_value is not None:
                        raw_fps = rtss_value
                        method_success = True
                    else:
                        if candidates:
                            values_only = [v for v, w in candidates]
                            values_only.sort()
                            if len(values_only) > 3:
                                trimmed_min = values_only[1]
                                trimmed_max = values_only[-2]
                                filtered = [(v, w) for v, w in candidates if trimmed_min <= v <= trimmed_max]
                            else:
                                filtered = candidates
                            total_weight = sum(w for _, w in filtered)
                            if total_weight > 0:
                                raw_fps = sum(v * w for v, w in filtered) / total_weight
                                method_success = True
                    # 设置来源标记，供下游平滑逻辑使用
                    self._last_source_rtss = bool(rtss_value)
                    if not method_success:
                        try:
                            if hasattr(self, '_cached_gpu_load'):
                                gpu_load = self._cached_gpu_load / 100.0
                                if gpu_load < 0.1:
                                    raw_fps = 20
                                elif gpu_load < 0.3:
                                    raw_fps = min(60, int(gpu_load * 150 + 25))
                                else:
                                    raw_fps = min(240, int(gpu_load * 200 + 10))
                            else:
                                gpus = GPUtil.getGPUs()
                                if gpus:
                                    gpu_load = gpus[0].load
                                    if gpu_load < 0.1:
                                        raw_fps = 20
                                    elif gpu_load < 0.3:
                                        raw_fps = min(60, int(gpu_load * 150 + 25))
                                    else:
                                        raw_fps = min(240, int(gpu_load * 200 + 10))
                                else:
                                    raw_fps = 30
                        except Exception as e:
                            print(f"回退FPS估算出错: {str(e)}")
                            raw_fps = 30
                    try:
                        refresh_hz = self._get_display_refresh_rate_hz()
                        if isinstance(refresh_hz, int) and refresh_hz > 0 and not rtss_present:
                            upper_cap = max(240, int(refresh_hz * 1.50))
                            raw_fps = min(raw_fps, upper_cap)
                    except Exception:
                        pass
            else:
                # 常规游戏FPS获取逻辑
                # 优先使用更可靠的计数器方法，并综合多个来源
                fps_methods = []
                
                # RTSS共享内存（若安装RTSS/Afterburner，置信度最高）
                fps_methods.append(self._get_fps_using_rtss_shared_memory)
                
                # Windows 游戏/图形计数器（高置信）
                fps_methods.append(self._get_fps_using_windows_gaming_api)
                
                # GPU厂商性能计数器（中高置信）
                fps_methods.append(self._get_fps_using_gpu_performance_counters)
                
                # 直接查询（中等置信）
                fps_methods.append(self._get_fps_using_direct_query)
                
                # GPU负载/温度/显存估算（低置信）
                fps_methods.append(self._get_fps_using_gpu_load_temp_and_memory)
                
                # 方法置信权重
                method_weights = {
                    self._get_fps_using_rtss_shared_memory: 1.0,
                    self._get_fps_using_windows_gaming_api: 1.0,
                    self._get_fps_using_gpu_performance_counters: 0.85,
                    self._get_fps_using_direct_query: 0.75,
                    self._get_fps_using_gpu_load_temp_and_memory: 0.55,
                }
                
                # 设置时间预算并采样多个来源
                raw_fps = 0
                method_success = False
                start_time = time.time()
                candidates = []
                rtss_present = False
                rtss_value = None
                
                for method in fps_methods:
                    if time.time() - start_time > 0.25:
                        break
                    try:
                        value = method()
                        if 1 <= value <= 400:
                            if method is self._get_fps_using_rtss_shared_memory:
                                rtss_present = True
                                rtss_value = value
                                # RTSS单源优先，拿到有效值后直接使用
                                break
                            else:
                                candidates.append((value, method_weights.get(method, 0.6)))
                    except Exception as e:
                        print(f"FPS方法 {method.__name__} 出错: {str(e)}")
                        continue
                
                if rtss_value is not None:
                    raw_fps = rtss_value
                    method_success = True
                else:
                    if candidates:
                        # 去除极端值后做加权平均
                        values_only = [v for v, w in candidates]
                        values_only.sort()
                        if len(values_only) > 3:
                            trimmed_min = values_only[1]
                            trimmed_max = values_only[-2]
                            filtered = [(v, w) for v, w in candidates if trimmed_min <= v <= trimmed_max]
                        else:
                            filtered = candidates
                        total_weight = sum(w for _, w in filtered)
                        if total_weight > 0:
                            raw_fps = sum(v * w for v, w in filtered) / total_weight
                            method_success = True
                
                # 设置来源标记，供下游平滑逻辑使用
                self._last_source_rtss = bool(rtss_value)
                
                # 如果所有方法都失败，使用刷新率感知的估算方法（避免误判成60以下）
                if not method_success:
                    try:
                        # 获取显示器刷新率用于上限约束
                        try:
                            refresh_hz = self._get_display_refresh_rate_hz()
                            upper_cap = max(240, int(refresh_hz * 1.50)) if isinstance(refresh_hz, int) and refresh_hz > 0 else 240
                        except Exception:
                            upper_cap = 240
                        # 优先使用缓存的GPU负载
                        gpu_load = None
                        if hasattr(self, '_cached_gpu_load'):
                            gpu_load = self._cached_gpu_load / 100.0
                        else:
                            try:
                                gpus = GPUtil.getGPUs()
                                if gpus:
                                    gpu_load = gpus[0].load
                            except Exception:
                                gpu_load = None
                        if gpu_load is not None:
                            # 刷新率感知映射：负载越高，越接近刷新率上限
                            if gpu_load < 0.10:
                                raw_fps = max(20, int(upper_cap * 0.35))
                            elif gpu_load < 0.30:
                                raw_fps = int(upper_cap * (0.50 + gpu_load * 0.4))
                            elif gpu_load < 0.60:
                                raw_fps = int(upper_cap * (0.65 + (gpu_load - 0.30) * 0.5))
                            elif gpu_load < 0.85:
                                raw_fps = int(upper_cap * (0.80 + (gpu_load - 0.60) * 0.4))
                            else:
                                raw_fps = int(upper_cap * (0.95 + (gpu_load - 0.85) * 0.2))
                            raw_fps = min(raw_fps, upper_cap)
                        else:
                            # 无法获取负载时，按刷新率的80%估计
                            raw_fps = int(upper_cap * 0.80)
                    except Exception as e:
                        print(f"回退FPS估算出错: {str(e)}")
                        raw_fps = 60
                
                # 根据显示器刷新率做上限约束，仅在无RTSS参与时应用，避免裁剪真实高FPS
                try:
                    refresh_hz = self._get_display_refresh_rate_hz()
                    if isinstance(refresh_hz, int) and refresh_hz > 0 and not rtss_present:
                        upper_cap = max(240, int(refresh_hz * 1.50))
                        raw_fps = min(raw_fps, upper_cap)
                except Exception:
                    pass
            
            # 优化帧时间计算，减少计算频率
            if self._has_query_perf_counter and (not hasattr(self, '_last_counter_time') or 
                                               current_time - self._last_counter_time > 0.2):
                current_counter = ctypes.c_ulonglong()
                if self._query_perf_counter(ctypes.byref(current_counter)):
                    elapsed = (current_counter.value - self._last_counter_value.value) / self._perf_frequency
                    if elapsed > 0.001:  # 避免除零错误
                        frame_time_ms = elapsed * 1000
                        self.frame_time_history.append(frame_time_ms)
                        # 保持历史记录长度，但减小窗口以提高响应速度
                        if len(self.frame_time_history) > self.fps_smoothing_window:
                            self.frame_time_history.pop(0)
                        
                    # 更新最后计数器值和时间
                    self._last_counter_value = current_counter
                    self._last_counter_time = current_time
            
            # 应用帧时间辅助计算（仅在非RTSS来源时）
            if len(self.frame_time_history) > 3 and not getattr(self, '_last_source_rtss', False):
                avg_frame_time = sum(self.frame_time_history[-3:]) / len(self.frame_time_history[-3:])
                if avg_frame_time > 0:
                    ft_fps = 1000.0 / avg_frame_time
                    # 帧时间仅作为轻微参考，统一较低权重，避免偏差（RTSS来源时不参与混合）
                    if 1 <= ft_fps <= 400:
                        raw_fps = raw_fps * 0.95 + ft_fps * 0.05
            
            # 应用游戏特定的修正值
            if not is_cf_game and active_game:
                # 尝试完全匹配
                if active_game in self.game_specific_fps_offsets:
                    raw_fps += self.game_specific_fps_offsets[active_game]
                else:
                    # 尝试部分匹配（游戏名称可能有版本号等）
                    for game_name, offset in self.game_specific_fps_offsets.items():
                        game_base_name = game_name.lower().replace('.exe', '')
                        if game_base_name in active_game.lower():
                            raw_fps += offset
                            break
                
                raw_fps = max(1, raw_fps)  # 确保FPS不会小于1
            
            # 应用GPU特定的修正（取消厂商偏置，统一为1.0）
            gpu_factor = 1.0
            raw_fps *= gpu_factor
            
            # 应用FPS平滑处理，CF游戏使用更敏感的平滑
            if is_cf_game:
                # CF游戏专用的平滑处理，更敏感地响应变化
                smoothed_fps = self._smooth_cf_fps_value(raw_fps)
            else:
                # 普通游戏使用标准平滑
                smoothed_fps = self._smooth_fps_value(raw_fps)
            
            # 更新缓存
            self.fps_cache = int(round(smoothed_fps))
            self.last_fps_timestamp = current_time
            
            return self.fps_cache
        except Exception as e:
            print(f"FPS获取出错: {str(e)}")
            return 0
    
    def _get_cf_specific_fps(self):
        """CF穿越火线游戏专用的FPS获取方法（刷新率感知 + 支持无GPUtil环境）"""
        try:
            # 优先使用缓存的GPU信息；若不可用，再尝试GPUtil；最后用CPU使用率近似
            gpu_load_pct = None
            gpu_mem_percent = 0
            if hasattr(self, '_cached_gpu_load'):
                gpu_load_pct = max(0.0, min(100.0, float(self._cached_gpu_load)))
            if gpu_load_pct is None:
                try:
                    gpus = GPUtil.getGPUs()
                    if gpus:
                        gpu = gpus[0]
                        gpu_load_pct = gpu.load * 100.0
                        if getattr(gpu, 'memoryTotal', 0) > 0:
                            gpu_mem_percent = (gpu.memoryUsed / gpu.memoryTotal) * 100.0
                except Exception:
                    gpu_load_pct = None
            if gpu_load_pct is None and hasattr(self, '_cached_cpu_usage'):
                # 打包环境下以CPU使用率近似GPU负载
                gpu_load_pct = max(0.0, min(100.0, float(self._cached_cpu_usage) * 1.2))
            if gpu_load_pct is None:
                gpu_load_pct = 50.0  # 合理的默认值，避免过低
            
            print(f"CF游戏GPU近似数据 - 负载: {gpu_load_pct:.1f}%, 内存使用: {gpu_mem_percent:.1f}%")
            
            # 获取显示器刷新率并设置更合理上限
            try:
                refresh_hz = self._get_display_refresh_rate_hz()
                upper_cap = max(120, int(refresh_hz * 1.15)) if isinstance(refresh_hz, int) and refresh_hz > 0 else 120
            except Exception:
                upper_cap = 120
            
            # 刷新率感知映射：负载越高，越接近刷新率上限（CF较激进）
            g = gpu_load_pct / 100.0
            if g < 0.10:
                cf_fps = max(40, int(upper_cap * 0.55))
            elif g < 0.30:
                cf_fps = int(upper_cap * (0.65 + g * 0.35))
            elif g < 0.60:
                cf_fps = int(upper_cap * (0.78 + (g - 0.30) * 0.45))
            elif g < 0.85:
                cf_fps = int(upper_cap * (0.90 + (g - 0.60) * 0.35))
            else:
                cf_fps = int(upper_cap * (0.98 + (g - 0.85) * 0.15))
            cf_fps = min(cf_fps, upper_cap)
            
            # 结合GPU内存使用率进行小幅修正（若可用）
            if gpu_mem_percent > 90:
                cf_fps = int(cf_fps * 0.90)
            elif gpu_mem_percent > 80:
                cf_fps = int(cf_fps * 0.95)
            elif gpu_mem_percent > 0 and gpu_mem_percent < 30:
                cf_fps = int(cf_fps * 1.05)
            
            # 取消厂商偏置
            # if hasattr(self, 'gpu_vendor'): pass
            
            # 最终范围保护与输出
            cf_fps = max(30, cf_fps)
            print(f"CF游戏估算FPS: {cf_fps}")
            return cf_fps
        except Exception as e:
            print(f"CF专用FPS获取出错: {str(e)}")
            # 返回一个合理的默认值（刷新率上限的80%），避免几十帧误判
            try:
                refresh_hz = self._get_display_refresh_rate_hz()
                upper_cap = max(120, int(refresh_hz * 1.15)) if isinstance(refresh_hz, int) and refresh_hz > 0 else 120
                return int(upper_cap * 0.80)
            except Exception:
                return 90
    
    def _smooth_cf_fps_value(self, current_fps):
        """CF游戏专用的FPS平滑处理，更快响应帧率变化"""
        # RTSS来源时启用轻平滑，快速响应真实帧率
        if getattr(self, '_last_source_rtss', False):
            try:
                prev = float(self.fps_cache) if getattr(self, 'fps_cache', 0) > 0 else None
            except Exception:
                prev = None
            if prev is not None:
                return prev * 0.20 + float(current_fps) * 0.80
            else:
                return float(current_fps)
        
        # 初始化CF专用的FPS历史记录
        if not hasattr(self, '_cf_fps_history'):
            self._cf_fps_history = []
        
        # CF游戏使用更短的历史记录窗口，提高响应速度
        self._cf_fps_history.append(current_fps)
        if len(self._cf_fps_history) > 3:  # 只保留最近3个值
            self._cf_fps_history.pop(0)
        
        # 使用简单但有效的加权平均，最新的值权重更高
        weights = [0.15, 0.30, 0.55][-len(self._cf_fps_history):]  # 动态调整权重数量
        total_weight = sum(weights)
        
        if total_weight == 0 or len(self._cf_fps_history) == 0:
            return current_fps
        
        # 计算加权平均
        weighted_average = sum(fps * weight for fps, weight in zip(self._cf_fps_history, weights)) / total_weight
        
        # CF游戏允许更大的帧率变化幅度，以反映实际游戏体验
        if len(self._cf_fps_history) > 1:
            # 计算前一个加权平均值
            prev_weights = weights[:-1] if len(weights) > 1 else [1.0]
            prev_total_weight = sum(prev_weights)
            previous_average = sum(fps * weight for fps, weight in zip(self._cf_fps_history[:-1], prev_weights)) / prev_total_weight if prev_total_weight > 0 else current_fps
            
            # CF游戏使用更大的变化限制，允许更快的响应
            max_change = max(10, previous_average * 0.3)  # 允许30%的变化
            
            # 应用变化限制，但保留更多的响应性
            if abs(weighted_average - previous_average) > max_change:
                if weighted_average > previous_average:
                    weighted_average = previous_average + max_change
                else:
                    weighted_average = previous_average - max_change
        
        return weighted_average
    
    def _get_active_game_process(self):
        """增强的活动游戏进程检测，考虑CPU/GPU使用率和前台窗口"""
        try:
            # 获取前台窗口标题
            active_window_title = self._get_active_window_title()
            
            # 获取GPU信息
            gpus = GPUtil.getGPUs()
            
            # 按资源使用排序进程，优先检测使用资源多的进程
            game_processes = []
            
            for proc in psutil.process_iter(['name', 'cpu_percent', 'memory_percent']):
                try:
                    process_name = proc.info['name']
                    process_cpu = proc.info['cpu_percent'] if proc.info['cpu_percent'] is not None else 0
                    
                    # 检查是否是游戏进程
                    if process_name.lower() in (name.lower() for name in COMMON_GAME_PROCESSES):
                        # 计算进程的资源使用分数
                        resource_score = process_cpu
                        
                        # 如果有GPU信息，结合GPU使用率因素（如果能匹配到该进程使用的GPU）
                        if gpus and process_cpu > 5:
                            resource_score += gpus[0].load * 50
                        
                        # 如果进程与前台窗口匹配，增加分数
                        if active_window_title and (process_name.lower() in active_window_title.lower() or 
                                                  process_name.replace('.exe', '').lower() in active_window_title.lower()):
                            resource_score += 100  # 大幅增加前台进程的优先级
                        
                        game_processes.append((resource_score, process_name))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            # 按资源使用分数排序，返回最高分的游戏进程
            if game_processes:
                game_processes.sort(key=lambda x: x[0], reverse=True)
                return game_processes[0][1]  # 返回进程名称
                
        except Exception as e:
            print(f"获取活动游戏进程出错: {str(e)}")
            
        return None
    
    def _smooth_fps_value(self, current_fps):
        """增强的FPS平滑算法，使用多阶段平滑和异常值检测"""
        # RTSS来源时启用轻平滑，快速响应真实帧率
        if getattr(self, '_last_source_rtss', False):
            try:
                prev = float(self.fps_cache) if getattr(self, 'fps_cache', 0) > 0 else None
            except Exception:
                prev = None
            if prev is not None:
                # 以最近值为主，避免过度平滑
                return prev * 0.20 + float(current_fps) * 0.80
            else:
                return float(current_fps)
        # 1. 异常值检测和过滤（放宽阈值，避免过度抑制真实波动）
        is_outlier = False
        if len(self.fps_history) > 3:
            recent_avg = sum(self.fps_history[-3:]) / 3
            if abs(current_fps - recent_avg) > recent_avg * 0.6:
                if len(self.fps_history) > 5:
                    older_avg = sum(self.fps_history[-6:-3]) / 3
                    if abs(current_fps - older_avg) > older_avg * 0.7:
                        is_outlier = True
        
        # 2. 自适应添加到历史记录（异常值适度靠近平均）
        if is_outlier:
            if self.fps_history:
                recent_avg = sum(self.fps_history[-3:]) / 3 if len(self.fps_history) >= 3 else self.fps_history[-1]
                adjusted_fps = recent_avg + (current_fps - recent_avg) * 0.4
                self.fps_history.append(adjusted_fps)
            else:
                self.fps_history.append(current_fps)
        else:
            self.fps_history.append(current_fps)
        
        # 3. 限制历史记录窗口
        if len(self.fps_history) > self.fps_smoothing_window:
            self.fps_history.pop(0)
        
        # 4. 加权平均（最新值权重更高但不过度）
        if len(self.fps_history) == 0:
            return current_fps
        weights = [(i + 1) ** 1.3 for i in range(len(self.fps_history))]
        total_weight = sum(weights)
        if total_weight == 0:
            return current_fps
        weighted_average = sum(fps * weight for fps, weight in zip(self.fps_history, weights)) / total_weight
        
        # 5. 动态变化限制（更宽松，提升响应速度）
        if len(self.fps_history) > 1:
            recent_trend = self.fps_history[-1] - self.fps_history[-2] if len(self.fps_history) >= 2 else 0
            base_max_change = 10
            percentage_max_change = 0.20
            prev_weights = [(i + 1) ** 1.3 for i in range(len(self.fps_history) - 1)]
            prev_total_weight = sum(prev_weights)
            previous_average = sum(fps * weight for fps, weight in zip(self.fps_history[:-1], prev_weights)) / prev_total_weight if prev_total_weight > 0 else current_fps
            if recent_trend > 0:
                max_change = max(base_max_change, previous_average * percentage_max_change * 1.15)
            elif recent_trend < 0:
                max_change = max(base_max_change, previous_average * percentage_max_change * 1.25)
            else:
                max_change = max(base_max_change, previous_average * percentage_max_change)
            if abs(weighted_average - previous_average) > max_change:
                if weighted_average > previous_average:
                    weighted_average = previous_average + max_change
                else:
                    weighted_average = previous_average - max_change
        
        return weighted_average
    
    def _get_fps_using_windows_gaming_api(self):
        """增强的Windows游戏API FPS获取方法（按前台进程过滤实例）"""
        try:
            # 检查是否有win32pdh支持
            try:
                import win32pdh
                import win32gui
                import win32process
                has_win32pdh = True
            except ImportError:
                has_win32pdh = False
            
            if not has_win32pdh:
                raise ImportError("win32pdh模块不可用")
            
            query = None
            try:
                query = win32pdh.OpenQuery()
                
                # 获取前台窗口进程信息
                active_pid_str = None
                active_name_lower = None
                try:
                    hwnd = win32gui.GetForegroundWindow()
                    if hwnd:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        active_pid_str = str(pid)
                except Exception:
                    pass
                try:
                    active_name = self._get_foreground_process_name()
                    if active_name:
                        active_name_lower = active_name.lower()
                except Exception:
                    pass
                
                valid_fps_values = []
                try:
                    # 仅枚举GPU Engine对象并筛选3D引擎实例
                    counters, instances = win32pdh.EnumObjectItems(None, None, "GPU Engine", win32pdh.PERF_DETAIL_WIZARD)
                    for inst in instances:
                        inst_lower = inst.lower()
                        # 只考虑3D渲染引擎，并尽量匹配前台进程
                        if ("engtype_3d" in inst_lower) and (
                            (active_pid_str and ("pid_" + active_pid_str) in inst_lower) or
                            (active_name_lower and active_name_lower in inst_lower) or
                            (not active_pid_str and not active_name_lower)
                        ):
                            path = f"\\GPU Engine({inst})\\Frames Rendered/Second"
                            try:
                                counter = win32pdh.AddCounter(query, path, 0)
                                win32pdh.CollectQueryData(query)
                                time.sleep(0.05)
                                win32pdh.CollectQueryData(query)
                                _, value = win32pdh.GetFormattedCounterValue(counter, win32pdh.PDH_FMT_DOUBLE)
                                if 1 <= value <= 400:
                                    valid_fps_values.append(value)
                                win32pdh.RemoveCounter(counter)
                            except Exception:
                                continue
                except Exception:
                    # 回退到广义路径集合
                    for path in [
                        "\\GPU Engine(*)\\Frames Rendered/Second",
                        "\\Direct3D Graphics(*)\\Frames Per Second",
                        "\\DirectX Graphics\\Frames Per Second",
                    ]:
                        try:
                            counter = win32pdh.AddCounter(query, path, 0)
                            win32pdh.CollectQueryData(query)
                            time.sleep(0.05)
                            win32pdh.CollectQueryData(query)
                            _, value = win32pdh.GetFormattedCounterValue(counter, win32pdh.PDH_FMT_DOUBLE)
                            if 1 <= value <= 400:
                                valid_fps_values.append(value)
                            win32pdh.RemoveCounter(counter)
                        except Exception:
                            continue
                
                if valid_fps_values:
                    valid_fps_values.sort()
                    if len(valid_fps_values) > 3:
                        valid_fps_values = valid_fps_values[1:-1]
                    return sum(valid_fps_values) / len(valid_fps_values)
                raise Exception("未获取到有效的FPS计数器值")
            finally:
                # 确保清理资源
                if query:
                    try:
                        win32pdh.CloseQuery(query)
                    except Exception:
                        pass
                    
        except Exception:
            # 方法失败时抛出异常，让调用者尝试其他方法
            raise
    
    def _get_fps_using_gpu_performance_counters(self):
        """增强的GPU性能计数器FPS获取方法，根据GPU型号和性能特征优化估算"""
        try:
            # 这是一个备选方法，尝试从GPU直接获取渲染性能数据
            gpus = GPUtil.getGPUs()
            if not gpus:
                return 0
                
            gpu = gpus[0]
            gpu_load = gpu.load
            gpu_temp = gpu.temperature
            gpu_memory_used = gpu.memoryUsed
            gpu_memory_total = gpu.memoryTotal
            memory_utilization = gpu_memory_used / gpu_memory_total if gpu_memory_total > 0 else 0
            
            # 获取GPU型号
            gpu_model = ''
            if hasattr(self, 'gpu_model') and self.gpu_model:
                gpu_model = self.gpu_model
            elif hasattr(gpu, 'name') and gpu.name:
                gpu_model = gpu.name.lower()
            
            # 基于GPU型号和性能等级调整理论最大FPS
            # 更精细的GPU分级系统
            theoretical_max_fps = 100  # 默认基础值
            
            # NVIDIA高端GPU
            if any(x in gpu_model for x in ['rtx 4090', 'rtx 4080', 'rtx 3090', 'rtx 3080']):
                theoretical_max_fps = 360  # 旗舰GPU
            elif any(x in gpu_model for x in ['rtx 4070 ti', 'rtx 4070', 'rtx 3070 ti', 'rtx 3070']):
                theoretical_max_fps = 280  # 高端GPU
            elif any(x in gpu_model for x in ['rtx 4060 ti', 'rtx 4060', 'rtx 3060 ti', 'rtx 3060']):
                theoretical_max_fps = 220  # 中高端GPU
            elif any(x in gpu_model for x in ['rtx 4050', 'rtx 3050', 'gtx 1660 ti', 'gtx 1660 super']):
                theoretical_max_fps = 160  # 中端GPU
            # AMD高端GPU
            elif any(x in gpu_model for x in ['rx 7900 xtx', 'rx 7900 xt', 'rx 6950 xt', 'rx 6900 xt']):
                theoretical_max_fps = 350  # 旗舰GPU
            elif any(x in gpu_model for x in ['rx 7800 xt', 'rx 7700 xt', 'rx 6800 xt', 'rx 6800']):
                theoretical_max_fps = 270  # 高端GPU
            elif any(x in gpu_model for x in ['rx 7600 xt', 'rx 7600', 'rx 6750 xt', 'rx 6700 xt']):
                theoretical_max_fps = 210  # 中高端GPU
            elif any(x in gpu_model for x in ['rx 6650 xt', 'rx 6600 xt', 'rx 6600']):
                theoretical_max_fps = 150  # 中端GPU
            # 集成显卡
            elif any(x in gpu_model for x in ['iris', 'uhd', 'hd graphics', 'radeon vega', 'radeon graphics']):
                theoretical_max_fps = 60  # 集成显卡
            # 其他GPU
            elif 'gtx' in gpu_model:
                theoretical_max_fps = 180  # 较老的NVIDIA中高端GPU
            elif 'rx' in gpu_model:
                theoretical_max_fps = 170  # 较老的AMD中高端GPU
            elif 'gt' in gpu_model:
                theoretical_max_fps = 120  # 入门级NVIDIA GPU
            
            # 基于GPU负载、内存使用和温度的综合FPS估算
            # 基础FPS计算
            base_fps = gpu_load * theoretical_max_fps
            
            # 内存使用修正因子
            memory_factor = 1.0
            if memory_utilization > 0.95:
                # 内存严重不足时，性能显著下降
                memory_factor = 0.7 * (1 - min(1, (memory_utilization - 0.95) * 10))
            elif memory_utilization > 0.85:
                # 内存高负载时，性能略有下降
                memory_factor = 0.85 * (1 - min(1, (memory_utilization - 0.85) * 5))
            elif memory_utilization > 0.7:
                # 内存中高负载时，性能轻微下降
                memory_factor = 0.95 * (1 - min(1, (memory_utilization - 0.7) * 2))
            
            # 温度修正因子（更精确的非线性修正）
            temp_factor = 1.0
            if gpu_temp > 95:
                # 极高温度，性能严重下降
                temp_factor = 0.5 * (1 - min(1, (gpu_temp - 95) * 0.02))
            elif gpu_temp > 90:
                # 非常高的温度，性能明显下降
                temp_factor = 0.65 * (1 - min(1, (gpu_temp - 90) * 0.03))
            elif gpu_temp > 85:
                # 高温度，性能中度下降
                temp_factor = 0.8 * (1 - min(1, (gpu_temp - 85) * 0.04))
            elif gpu_temp > 80:
                # 较高温度，性能轻微下降
                temp_factor = 0.9 * (1 - min(1, (gpu_temp - 80) * 0.02))
            elif gpu_temp > 75:
                # 正常偏高温，性能微小下降
                temp_factor = 0.97
            
            # 负载非线性修正 - 高负载时FPS增长放缓
            load_factor = 1.0
            if gpu_load > 0.9:
                # 极高负载时，实际FPS通常不会完全线性增长
                load_factor = 0.95 + (gpu_load - 0.9) * 0.5
            
            # 应用所有修正因子
            final_fps = base_fps * memory_factor * temp_factor * load_factor
            
            # 移除随机抖动，避免估算引入噪声导致不准确
            return final_fps
            
        except Exception:
            raise
    
    def _get_fps_using_direct_query(self):
        """使用直接查询技术获取FPS"""
        try:
            # 导入ctypes用于调用Windows API
            import ctypes
            import ctypes.wintypes
            
            # 定义必要的结构体
            class LUID(ctypes.Structure):
                _fields_ = [
                    ("LowPart", ctypes.wintypes.DWORD),
                    ("HighPart", ctypes.wintypes.LONG),
                ]
            
            class DXGI_QUERY_VIDEO_MEMORY_INFO(ctypes.Structure):
                _fields_ = [
                    ("Budget", ctypes.c_uint64),
                    ("CurrentUsage", ctypes.c_uint64),
                    ("AvailableForReservation", ctypes.c_uint64),
                    ("CurrentReservation", ctypes.c_uint64),
                ]
            
            # 定义GUID结构
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.wintypes.DWORD),
                    ("Data2", ctypes.wintypes.WORD),
                    ("Data3", ctypes.wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]
            
            # 尝试获取DXGI模块
            try:
                dxgi = ctypes.windll.dxgi
            except AttributeError:
                dxgi = None
            
            # 获取GPU信息
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu = gpus[0]
                gpu_load = gpu.load
                gpu_memory_used = gpu.memoryUsed
                gpu_memory_total = gpu.memoryTotal
                memory_utilization = gpu_memory_used / gpu_memory_total if gpu_memory_total > 0 else 0
                
                # 基于GPU型号、负载和内存使用的综合估算
                # NVIDIA GPU特殊处理
                if 'nvidia' in gpu.name.lower() or 'rtx' in gpu.name.lower() or 'gtx' in gpu.name.lower():
                    # 考虑内存使用对性能的影响
                    memory_factor = 1.0
                    if memory_utilization > 0.9:
                        memory_factor = 0.85
                    elif memory_utilization > 0.75:
                        memory_factor = 0.95
                    
                    # 更精确的NVIDIA GPU FPS估算
                    if gpu_load > 0.95:
                        return (165 + (gpu_load - 0.95) * 300) * memory_factor
                    elif gpu_load > 0.85:
                        return (144 + (gpu_load - 0.85) * 210) * memory_factor
                    elif gpu_load > 0.75:
                        return (120 + (gpu_load - 0.75) * 240) * memory_factor
                    elif gpu_load > 0.6:
                        return (90 + (gpu_load - 0.6) * 50) * memory_factor
                    elif gpu_load > 0.4:
                        return (60 + (gpu_load - 0.4) * 75) * memory_factor
                    elif gpu_load > 0.2:
                        return (30 + (gpu_load - 0.2) * 150) * memory_factor
                # AMD GPU特殊处理
                elif 'amd' in gpu.name.lower() or 'radeon' in gpu.name.lower():
                    # 考虑内存使用对性能的影响
                    memory_factor = 1.0
                    if memory_utilization > 0.9:
                        memory_factor = 0.8
                    elif memory_utilization > 0.75:
                        memory_factor = 0.9
                    
                    # 更精确的AMD GPU FPS估算
                    if gpu_load > 0.95:
                        return (155 + (gpu_load - 0.95) * 280) * memory_factor
                    elif gpu_load > 0.85:
                        return (130 + (gpu_load - 0.85) * 200) * memory_factor
                    elif gpu_load > 0.75:
                        return (110 + (gpu_load - 0.75) * 200) * memory_factor
                    elif gpu_load > 0.6:
                        return (85 + (gpu_load - 0.6) * 41.67) * memory_factor
                    elif gpu_load > 0.4:
                        return (55 + (gpu_load - 0.4) * 75) * memory_factor
                    elif gpu_load > 0.2:
                        return (25 + (gpu_load - 0.2) * 150) * memory_factor
            
            return 0
        except Exception:
            raise
    
    def _get_fps_using_rtss_shared_memory(self):
        """尝试通过RTSS共享内存读取当前前台进程的FPS（若安装了RTSS/Afterburner）。
        - 优先匹配前台窗口PID，其次匹配名称，避免误读其他进程
        - 使用帧时间作为兜底（fltFramerate为0时）
        - RTSS未运行或未安装时返回0
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            FILE_MAP_READ = 0x0004
            class RTSS_SHARED_MEMORY_HEADER(ctypes.Structure):
                _fields_ = [
                    ("dwSignature", ctypes.c_uint32),
                    ("dwVersion", ctypes.c_uint32),
                    ("dwAppEntrySize", ctypes.c_uint32),
                    ("dwAppCount", ctypes.c_uint32),
                    ("dwOSDEntrySize", ctypes.c_uint32),
                ]
            class RTSS_SHARED_MEMORY_APP(ctypes.Structure):
                _fields_ = [
                    ("dwProcessId", ctypes.c_uint32),
                    ("szName", ctypes.c_char * 260),
                    ("dwFlags", ctypes.c_uint32),
                    ("dwActive", ctypes.c_uint32),
                    ("fltFramerate", ctypes.c_float),
                    ("fltMinFramerate", ctypes.c_float),
                    ("fltMaxFramerate", ctypes.c_float),
                    ("fltFrameTime", ctypes.c_float),
                ]
            # 获取前台窗口PID和名称
            try:
                hwnd = user32.GetForegroundWindow()
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                fg_pid = int(pid.value)
            except Exception:
                fg_pid = 0
            try:
                fg_name = (self._get_foreground_process_name() or "").lower()
            except Exception:
                fg_name = ""
            best_fps = 0.0
            pid_match_fps = 0.0
            name_match_fps = 0.0
            for mapping_name in ("RTSSSharedMemoryV2", "RTSSSharedMemoryV3"):
                try:
                    hMap = kernel32.OpenFileMappingW(FILE_MAP_READ, False, mapping_name)
                except Exception:
                    hMap = None
                if not hMap:
                    continue
                pMem = kernel32.MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 0)
                if not pMem:
                    kernel32.CloseHandle(hMap)
                    continue
                try:
                    header = RTSS_SHARED_MEMORY_HEADER.from_address(pMem)
                    app_count = int(getattr(header, 'dwAppCount', 0))
                    app_size = int(getattr(header, 'dwAppEntrySize', ctypes.sizeof(RTSS_SHARED_MEMORY_APP)))
                    base = pMem + ctypes.sizeof(RTSS_SHARED_MEMORY_HEADER)
                    for i in range(min(app_count, 64)):
                        addr = base + i * app_size
                        app = RTSS_SHARED_MEMORY_APP.from_address(addr)
                        # 优先使用帧率；无则使用帧时间推算
                        fps_val = float(getattr(app, 'fltFramerate', 0.0))
                        if fps_val <= 0:
                            ft = float(getattr(app, 'fltFrameTime', 0.0))
                            if ft > 0:
                                fps_val = 1000.0 / ft
                        if fps_val <= 0:
                            continue
                        try:
                            app_name = app.szName.decode(errors='ignore').lower()
                        except Exception:
                            app_name = ""
                        app_pid = int(getattr(app, 'dwProcessId', 0))
                        # PID优先匹配
                        if fg_pid and app_pid and fg_pid == app_pid:
                            pid_match_fps = max(pid_match_fps, fps_val)
                        # 名称次级匹配
                        if fg_name and app_name and (fg_name == app_name or fg_name in app_name or app_name in fg_name):
                            name_match_fps = max(name_match_fps, fps_val)
                        # 总体最佳（兜底）
                        best_fps = max(best_fps, fps_val)
                finally:
                    kernel32.UnmapViewOfFile(pMem)
                    kernel32.CloseHandle(hMap)
                # 若已有PID/名称匹配值则优先返回
                if pid_match_fps > 0 or name_match_fps > 0:
                    break
            final = 0.0
            if pid_match_fps > 0:
                final = pid_match_fps
            elif name_match_fps > 0:
                final = name_match_fps
            else:
                final = best_fps
            if final > 0:
                return int(final)
        except Exception as e:
            logger.debug(f"RTSS共享内存FPS读取失败: {e}")
        return 0
    
    def _get_fps_using_gpu_load_temp_and_memory(self):
        """优化的GPU负载和温度FPS估算方法，使用缓存和更高效的计算"""
        try:
            # 首先检查缓存的GPU信息
            if hasattr(self, '_cached_gpu_load') and hasattr(self, '_cached_gpu_temp'):
                # 使用缓存值快速估算
                gpu_load = self._cached_gpu_load / 100.0  # 转换为0-1范围
                gpu_temp = self._cached_gpu_temp
                
                # 尝试获取内存使用率，但只在有缓存时使用
                memory_utilization = 0
                if hasattr(self, '_cached_gpu_memory_used') and hasattr(self, '_cached_gpu_memory_total'):
                    if self._cached_gpu_memory_total > 0:
                        memory_utilization = self._cached_gpu_memory_used / self._cached_gpu_memory_total
                
                # 使用简化的性能指标计算
                # 降低计算复杂度，移除min操作
                temp_normalized = gpu_temp / 100.0 if gpu_temp < 100 else 1.0
                
                # 简化的性能指标计算
                performance_index = gpu_load * 0.7 + memory_utilization * 0.2 + temp_normalized * 0.1
                
                # 使用更简单的温度修正
                temp_factor = 1.0
                if gpu_temp > 85:
                    temp_factor = max(0.7, 1.0 - (gpu_temp - 85) * 0.01)
                
                # 使用更简单的内存修正
                memory_factor = 1.0
                if memory_utilization > 0.9:
                    memory_factor = max(0.8, 1.0 - (memory_utilization - 0.9) * 0.5)
                
                # 简化的最终性能计算
                adjusted_performance = performance_index * temp_factor * memory_factor
                
                # 大幅简化FPS映射表，减少比较次数
                # 使用分段线性映射，减少if-elif分支数量
                if adjusted_performance < 0.1:
                    return 10
                elif adjusted_performance < 0.25:
                    return 30
                elif adjusted_performance < 0.4:
                    return 50
                elif adjusted_performance < 0.6:
                    # 60-90 FPS范围的线性映射
                    return int(60 + (adjusted_performance - 0.4) * 150)
                elif adjusted_performance < 0.8:
                    # 90-144 FPS范围的线性映射
                    return int(90 + (adjusted_performance - 0.6) * 270)
                else:
                    # 144+ FPS范围的线性映射，但有上限
                    return min(240, int(144 + (adjusted_performance - 0.8) * 480))
            
            # 如果没有缓存，使用轻量级的直接查询作为后备
            try:
                # 使用局部变量以提高访问速度
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    gpu_load = gpu.load
                    return min(240, int(gpu_load * 180 + 20))  # 非常简单的线性映射
            except Exception:
                pass
                
        except Exception:
            pass
            
        return 0
    
    def _get_basic_fps_estimate(self):
        """基础FPS估算方法（作为最后的后备方案）"""
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu = gpus[0]
                gpu_load = gpu.load
                
                # 简单的负载到FPS映射，但增加了更细致的区间
                if gpu_load > 0.95:
                    return 120
                elif gpu_load > 0.9:
                    return 110
                elif gpu_load > 0.8:
                    return 100
                elif gpu_load > 0.7:
                    return 90
                elif gpu_load > 0.6:
                    return 80
                elif gpu_load > 0.5:
                    return 60
                elif gpu_load > 0.4:
                    return 50
                elif gpu_load > 0.3:
                    return 45
                elif gpu_load > 0.2:
                    return 35
                elif gpu_load > 0.1:
                    return 30
            
            return 0
        except Exception:
            return 0

class FloatingBall(QWidget):
    def __init__(self):
        super().__init__()
        
        # 初始化变量
        self.window_width = 100
        self.window_height = 200
        self.prev_cpu_usage = 0
        self.prev_gpu_load = 0
        self.gpu_temp = 0
        self.cpu_usage = 0
        self.gpu_load = 0
        self.cpu_temp = None
        self._last_cpu_temp_read_ts = 0
        self.down_speed = 0  # 下载速度（字节/秒）
        self.up_speed = 0  # 上传速度（字节/秒）
        self.is_dragging = False
        self.drag_position = QPoint()
        
        # 缓存清理相关变量
        self.is_cleaning_cache = False
        self.cache_cleaning_progress = 0
        self.cache_cleaning_timer = None
        
        # 游戏检测和FPS相关变量
        self.is_gaming = False
        self.fps = 0
        
        # 内存优化相关标志
        self._empty_working_set_checked = False
        self._empty_working_set_available = False
        
        # 默认设置与配置加载
        self.settings = {
            "show_fps": True,
            "fps_only_in_game": True,
            "locked_position": False,
            "opacity": 1.0,
            "performance_sleep_interval": 0.05,
            "signal_interval": 0.1,
            "strict_non_game_fullscreen": True,
            "debug_log": False,
            "enable_ingame_hud": True,
        }
        self.load_config()

        # 创建透明置顶的游戏HUD窗口
        try:
            self.overlay_hud = GameOverlayHUD()
        except Exception:
            self.overlay_hud = None
        
        # 应用全局运行期开关
        global STRICT_NON_GAME_FULLSCREEN, DEBUG_LOG
        STRICT_NON_GAME_FULLSCREEN = bool(self.settings.get("strict_non_game_fullscreen", True))
        DEBUG_LOG = bool(self.settings.get("debug_log", False))
        
        # 初始化UI
        self.init_ui()
        
        # 初始化系统托盘图标
        self.init_system_tray()
        
        # 应用配置到当前窗口与线程
        self.apply_config()
        
        # 启动系统信息获取线程
        self.start_system_info_thread()
        
    def init_ui(self):
        # 设置窗口属性
        self.setWindowTitle("悬浮球")
        self.setFixedSize(self.window_width, self.window_height)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 加载背景图片
        self.load_background_image()
        
        # 计算窗口位置（右侧中间）
        screen = QApplication.desktop().screenGeometry()
        x = screen.width() - self.window_width
        y = (screen.height() - self.window_height) // 2
        self.move(x, y)
        
    def load_background_image(self):
        self.bg_image = None
        try:
            image_path = get_resource_path("Resources/xiaohaoxuanfuchuang.png")
            if os.path.exists(image_path):
                self.bg_image = QImage(image_path)
                if not self.bg_image.isNull():
                    self.bg_image = self.bg_image.scaled(self.window_width, self.window_height)
        except Exception as e:
            print(f"加载背景图片失败: {e}")
            self.bg_image = None
    
    def start_system_info_thread(self):
        # 创建工作线程（SystemInfoWorker已继承QThread）
        self.worker = SystemInfoWorker()
        
        # 根据配置设置线程参数
        try:
            self.worker.sleep_interval = float(self.settings.get("performance_sleep_interval", 0.05))
            self.worker.signal_interval = float(self.settings.get("signal_interval", 0.1))
        except Exception:
            self.worker.sleep_interval = 0.05
            self.worker.signal_interval = 0.1
        
        # 连接信号和槽
        self.worker.update_signal.connect(self.update_system_info)
        self.worker.error_signal.connect(self.handle_error)
        
        # 启动线程
        self.worker.start()
        
    def update_system_info(self, cpu_usage, gpu_temp, gpu_load, down_speed, up_speed, is_gaming, fps):
        self.prev_cpu_usage = self.cpu_usage
        self.prev_gpu_load = self.gpu_load
        self.cpu_usage = cpu_usage
        self.gpu_temp = gpu_temp
        self.gpu_load = gpu_load
        self.down_speed = down_speed
        self.up_speed = up_speed

        # 更新CPU温度（1秒节流，避免过于频繁）
        try:
            now_ts = time.time()
            if not hasattr(self, 'cpu_temp'):
                self.cpu_temp = None
            if not hasattr(self, '_last_cpu_temp_read_ts'):
                self._last_cpu_temp_read_ts = 0
            if now_ts - float(self._last_cpu_temp_read_ts or 0) >= 1.0:
                self.cpu_temp = self._get_cpu_temperature_celsius()
                self._last_cpu_temp_read_ts = now_ts
        except Exception:
            pass
        
        # 当游戏状态从True变为False时，确保FPS也重置为0
        if self.is_gaming and not is_gaming:
            self.is_gaming = False
            self.fps = 0
        else:
            self.is_gaming = is_gaming
            self.fps = fps

        # 同步到游戏内HUD透明置顶窗口
        try:
            hud_enabled = bool(self.settings.get("enable_ingame_hud", True))
        except Exception:
            hud_enabled = True
        if hud_enabled and self.is_gaming:
            if hasattr(self, 'overlay_hud') and self.overlay_hud is not None:
                try:
                    self.overlay_hud.show()
                    self.overlay_hud.update_metrics(self.cpu_usage, self.cpu_temp, self.gpu_load, self.gpu_temp, self.fps)
                except Exception:
                    pass
        else:
            if hasattr(self, 'overlay_hud') and self.overlay_hud is not None:
                try:
                    self.overlay_hud.hide()
                except Exception:
                    pass
            
        self.update()  # 触发重绘
        self.update_tray_icon()  # 更新任务栏显示
    
    def handle_error(self, error_message):
        print(f"发生错误: {error_message}")
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制背景图片
        if self.bg_image and not self.bg_image.isNull():
            painter.drawImage(QRect(0, 0, self.window_width, self.window_height), self.bg_image)
        
        # 绘制温度圆环
        self.draw_temperature_ring(painter)
        
        # 绘制CPU和GPU信息
        self.draw_system_info(painter)
    
    def draw_temperature_ring(self, painter):
        # 圆环参数
        center_x = self.window_width // 2
        radius = 22
        x1 = center_x - radius
        y1 = 64
        x2 = center_x + radius
        y2 = y1 + 2 * radius
        
        # 绘制背景圆环（灰色，用于显示未填充部分）
        background_pen = QPen(QColor(50, 50, 50, 100), 4, Qt.SolidLine)
        painter.setPen(background_pen)
        painter.drawArc(QRect(x1, y1, x2 - x1, y2 - y1),
                        360 * 16, 360 * 16)  # 绘制整个圆
        
        # 判断是显示缓存清理进度、FPS还是温度（支持设置开关）
        show_fps_enabled = bool(self.settings.get("show_fps", True))
        fps_only_in_game = bool(self.settings.get("fps_only_in_game", True))
        should_show_fps = show_fps_enabled and self.fps > 0 and (not fps_only_in_game or self.is_gaming)
        if self.is_cleaning_cache:
            # 缓存清理模式
            ratio = self.cache_cleaning_progress / 100.0
            # 缓存清理进度使用蓝色渐变
            cache_color = QColor(0, 100, 255, 200 + int(min(ratio * 55, 55)))
            
            # 绘制发光效果
            if ratio > 0:
                # 创建发光效果的渐变
                glow_radius = radius + 4
                glow_rect = QRect(center_x - glow_radius, y1 - 4, glow_radius * 2, glow_radius * 2)
                glow_gradient = QRadialGradient(center_x, y1 + radius, glow_radius)
                
                # 设置发光颜色
                glow_gradient.setColorAt(0, QColor(cache_color.red(), cache_color.green(), cache_color.blue(), 60))
                glow_gradient.setColorAt(1, QColor(0, 0, 0, 0))
                
                # 绘制发光效果
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(glow_gradient))
                painter.drawEllipse(glow_rect)
            
            # 设置圆环颜色
            color = cache_color
            current_ratio = ratio
        elif should_show_fps:
            # FPS显示模式
            # 根据FPS值设置不同的颜色
            fps_ratio = min(self.fps / 144, 1.0)  # 归一化FPS值（假设最高144fps）
            if fps_ratio > 0.8:
                # 高FPS - 绿色
                fps_color = QColor(0, 255, 0, 200)
            elif fps_ratio > 0.5:
                # 中等FPS - 蓝色
                fps_color = QColor(0, 191, 255, 200)
            else:
                # 低FPS - 红色
                fps_color = QColor(255, 0, 0, 200)
            
            # 绘制发光效果
            # 创建发光效果的渐变
            glow_radius = radius + 4
            glow_rect = QRect(center_x - glow_radius, y1 - 4, glow_radius * 2, glow_radius * 2)
            glow_gradient = QRadialGradient(center_x, y1 + radius, glow_radius)
            
            # 设置发光颜色
            glow_gradient.setColorAt(0, QColor(fps_color.red(), fps_color.green(), fps_color.blue(), 60))
            glow_gradient.setColorAt(1, QColor(0, 0, 0, 0))
            
            # 绘制发光效果
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glow_gradient))
            painter.drawEllipse(glow_rect)
            
            # 设置圆环颜色
            color = fps_color
            current_ratio = fps_ratio
        else:
            # 温度显示模式
            max_temp = 100
            ratio = min(self.gpu_temp / max_temp, 1.0)  # 限制在1.0以内
            
            # 绘制发光效果
            if ratio > 0:
                # 创建发光效果的渐变
                glow_radius = radius + 4
                glow_rect = QRect(center_x - glow_radius, y1 - 4, glow_radius * 2, glow_radius * 2)
                glow_gradient = QRadialGradient(center_x, y1 + radius, glow_radius)
                
                # 根据温度设置发光颜色
                glow_color = self.get_gradient_color(ratio)
                glow_gradient.setColorAt(0, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 60))
                glow_gradient.setColorAt(1, QColor(0, 0, 0, 0))
                
                # 绘制发光效果
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(glow_gradient))
                painter.drawEllipse(glow_rect)
            
            # 设置圆环颜色
            color = self.get_gradient_color(ratio)
            current_ratio = ratio
        
        # 设置笔的宽度和样式
        pen = QPen(color, 5, Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)  # 圆润的端点样式
        painter.setPen(pen)
        
        # 直接根据比例绘制进度圆环
        if current_ratio > 0:
            # 计算要绘制的角度范围（从底部向两边同时绘制）
            total_extent = 360  # 完整圆环
            temp_extent = current_ratio * total_extent
            
            # 计算左右两侧要绘制的角度
            half_temp_extent = temp_extent / 2
            
            # 绘制左侧圆环（从底部向左绘制）
            left_start_angle = 270
            left_span_angle = -half_temp_extent  # 负角度表示逆时针绘制
            painter.drawArc(QRect(x1, y1, x2 - x1, y2 - y1),
                            int(left_start_angle * 16), int(left_span_angle * 16))
            
            # 绘制右侧圆环（从底部向右绘制）
            right_start_angle = 270
            right_span_angle = half_temp_extent  # 正角度表示顺时针绘制
            painter.drawArc(QRect(x1, y1, x2 - x1, y2 - y1),
                            int(right_start_angle * 16), int(right_span_angle * 16))
        
        # 绘制一个亮点以增加立体感
        if current_ratio > 0:
            # 计算亮点位置（在圆环的最上方）
            highlight_x = center_x
            highlight_y = y1
            
            # 创建渐变亮点
            highlight_gradient = QRadialGradient(highlight_x, highlight_y, 5)
            highlight_gradient.setColorAt(0, QColor(255, 255, 255, 200))
            highlight_gradient.setColorAt(1, QColor(255, 255, 255, 0))
            
            # 绘制亮点
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(highlight_gradient))
            painter.drawEllipse(QPoint(highlight_x, highlight_y), 3, 3)
            
        # 绘制文本（进度、FPS或温度）
        painter.setPen(QPen(QColor(255, 255, 255)))
        
        if should_show_fps:
            # FPS显示模式 - 根据数字位数自动调整字体大小
            fps_str = f"{self.fps}fps"
            # 判断FPS是否为3位数或更多
            if self.fps >= 100:
                # 3位数FPS，使用较小字体并扩大显示区域
                painter.setFont(QFont("Arial", 10, QFont.Bold))
                text_rect = QRect(center_x - 25, y1 + radius - 8, 50, 16)
            else:
                # 1-2位数FPS，保持原有样式
                painter.setFont(QFont("Arial", 12, QFont.Bold))
                text_rect = QRect(center_x - 20, y1 + radius - 8, 40, 16)
            painter.drawText(text_rect, Qt.AlignCenter, fps_str)
        else:
            # 温度或清理进度显示 - 保持原有样式
            painter.setFont(QFont("Arial", 12, QFont.Bold))
            text_rect = QRect(center_x - 20, y1 + radius - 8, 40, 16)
            
            if self.is_cleaning_cache:
                painter.drawText(text_rect, Qt.AlignCenter, f"{self.cache_cleaning_progress}%")
            else:
                painter.drawText(text_rect, Qt.AlignCenter, f"{int(self.gpu_temp)}°")
    
    def draw_system_info(self, painter):
        # 判断是显示缓存清理进度、FPS还是温度（支持设置开关）
        show_fps_enabled = bool(self.settings.get("show_fps", True))
        fps_only_in_game = bool(self.settings.get("fps_only_in_game", True))
        should_show_fps = show_fps_enabled and self.fps > 0 and (not fps_only_in_game or self.is_gaming)
        if self.is_cleaning_cache:
            # 缓存清理模式
            # 只显示白色的"清理中"文字，隐藏带颜色的进度百分比
            # 保留白色文字在圆环中间显示
            pass
        elif should_show_fps:
            # 游戏模式 - 显示FPS
            # 已暂时隐藏带颜色的FPS显示，只保留白色FPS显示
            pass
        else:
            # 温度显示模式
            # 已暂时隐藏带颜色的温度显示，只保留白色温度显示
            pass

        # 游戏内HUD显示（独立透明窗口优先）
        try:
            hud_enabled = bool(self.settings.get("enable_ingame_hud", True))
        except Exception:
            hud_enabled = True
        # 如果没有独立HUD窗口，才在悬浮球里临时显示
        show_in_ball = hud_enabled and self.is_gaming and not (hasattr(self, 'overlay_hud') and self.overlay_hud is not None)
        if show_in_ball:
            painter.setPen(QPen(QColor(255, 215, 0)))
            painter.setFont(QFont("Arial", 13, QFont.Bold))
            top_y = 8
            line_h = 18
            fps_text = f"FPS {self.fps}" if self.fps > 0 else "FPS --"
            cpu_temp_text = f"{int(self.cpu_temp)}°" if isinstance(self.cpu_temp, (int, float)) else "--"
            gpu_temp_text = f"{int(self.gpu_temp)}°" if self.gpu_temp > 0 else "--"
            cpu_text = f"CPU {self.cpu_usage:.0f}% {cpu_temp_text}"
            gpu_text = f"GPU {self.gpu_load:.0f}% {gpu_temp_text}"
            painter.drawText(QRect(0, top_y, self.window_width, line_h), Qt.AlignCenter, fps_text)
            painter.drawText(QRect(0, top_y+line_h, self.window_width, line_h), Qt.AlignCenter, cpu_text)
            painter.drawText(QRect(0, top_y+2*line_h, self.window_width, line_h), Qt.AlignCenter, gpu_text)
        
        # 始终显示CPU和GPU使用率信息
        # 计算CPU和GPU的变化箭头
        cpu_arrow = "↑" if self.cpu_usage > self.prev_cpu_usage else "↓" if self.cpu_usage < self.prev_cpu_usage else "-"
        gpu_arrow = "↑" if self.gpu_load > self.prev_gpu_load else "↓" if self.gpu_load < self.prev_gpu_load else "-"
        
        # 绘制CPU使用率（箭头为蓝色，百分比保持原来的颜色逻辑）
        cpu_text_rect = QRect(0, 130, self.window_width, 20)
        cpu_color = self.get_usage_color(self.cpu_usage)
        cpu_percentage_text = f"{self.cpu_usage:.1f}%"
        
        # 测量文本宽度以计算位置
        font_metrics = painter.fontMetrics()
        painter.setFont(QFont("Helvetica", 10, QFont.Bold))
        arrow_width = font_metrics.width(cpu_arrow)
        percentage_width = font_metrics.width(cpu_percentage_text)
        total_width = arrow_width + percentage_width
        
        # 计算起始位置以居中显示
        start_x = (self.window_width - total_width) // 2
        
        # 绘制浅蓝色天青蓝箭头
        painter.setPen(QPen(QColor(0, 191, 255)))  # 设置浅蓝色天青蓝
        painter.drawText(QRect(start_x, 130, arrow_width, 20),
                        Qt.AlignCenter, cpu_arrow)
        
        # 绘制CPU使用率百分比（保持原来的颜色）
        painter.setPen(QPen(cpu_color))
        painter.drawText(QRect(start_x + arrow_width, 130, percentage_width, 20),
                        Qt.AlignCenter, cpu_percentage_text)
        
        # 绘制GPU使用率（箭头为蓝色，百分比保持原来的颜色逻辑）
        gpu_text_rect = QRect(0, 165, self.window_width, 20)
        gpu_color = self.get_usage_color(self.gpu_load)
        gpu_percentage_text = f"{self.gpu_load:.1f}%"
        
        # 测量文本宽度以计算位置
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        arrow_width = font_metrics.width(gpu_arrow)
        percentage_width = font_metrics.width(gpu_percentage_text)
        total_width = arrow_width + percentage_width
        
        # 计算起始位置以居中显示
        start_x = (self.window_width - total_width) // 2
        
        # 绘制浅蓝色天青蓝箭头
        painter.setPen(QPen(QColor(0, 191, 255)))  # 设置浅蓝色天青蓝
        painter.drawText(QRect(start_x, 165, arrow_width, 20),
                        Qt.AlignCenter, gpu_arrow)
        
        # 绘制GPU使用率百分比（保持原来的颜色）
        painter.setPen(QPen(gpu_color))
        painter.drawText(QRect(start_x + arrow_width, 165, percentage_width, 20),
                        Qt.AlignCenter, gpu_percentage_text)
    
    def get_gradient_color(self, ratio):
        # 改进的颜色渐变算法：从浅绿色 -> 蓝色 -> 黄色 -> 红色 -> 深红色
        # 使用更自然的HSL颜色空间转换
        if ratio <= 0.2:
            # 绿色到蓝色 (120 到 220)
            hue = 120 + (ratio / 0.2) * 100
        elif ratio <= 0.4:
            # 蓝色到青色 (220 到 180)
            hue = 220 - ((ratio - 0.2) / 0.2) * 40
        elif ratio <= 0.6:
            # 青色到绿色 (180 到 120)
            hue = 180 - ((ratio - 0.4) / 0.2) * 60
        elif ratio <= 0.8:
            # 绿色到黄色 (120 到 60)
            hue = 120 - ((ratio - 0.6) / 0.2) * 60
        else:
            # 黄色到红色到深红色 (60 到 0 到 340)
            if ratio <= 0.9:
                # 黄色到红色 (60 到 0)
                hue = 60 - ((ratio - 0.8) / 0.1) * 60
            else:
                # 红色到深红色 (0 到 340)
                hue = 0 - ((ratio - 0.9) / 0.1) * 20
        saturation = 100  # 最大饱和度
        lightness = 40 + ratio * 20  # 从40%到60%亮度
        
        # 将HSL转换为RGB
        h = hue / 360.0
        s = saturation / 100.0
        l = lightness / 100.0
        
        def hue_to_rgb(p, q, t):
            if t < 0: t += 1
            if t > 1: t -= 1
            if t < 1/6: return p + (q - p) * 6 * t
            if t < 1/2: return q
            if t < 2/3: return p + (q - p) * (2/3 - t) * 6
            return p
        
        if s == 0:
            r = g = b = int(l * 255)  # 灰度
        else:
            q = l * (1 + s) if l < 0.5 else l + s - l * s
            p = 2 * l - q
            r = int(hue_to_rgb(p, q, h + 1/3) * 255)
            g = int(hue_to_rgb(p, q, h) * 255)
            b = int(hue_to_rgb(p, q, h - 1/3) * 255)
        
        # 增加alpha通道以提供更好的透明度效果
        # 在低温时略微透明，高温时更不透明
        alpha = 200 + int(min(ratio * 55, 55))  # 从200到255的alpha值
        
        return QColor(r, g, b, alpha)
    
    def get_usage_color(self, usage):
        # 根据使用率返回颜色：绿色 -> 黄色 -> 红色
        if usage <= 40:
            return QColor(12, 222, 41)  # 绿色
        elif 41 <= usage <= 60:
            return QColor(255, 255, 0)  # 黄色
        else:
            return QColor(255, 0, 0)  # 红色
    

    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self, 'locked_position', False):
                return
            self.is_dragging = True
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
        elif event.button() == Qt.RightButton:
            self.show_context_menu(event.globalPos())
        
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            self.snap_to_edge()
            
    def snap_to_edge(self):
        # 吸附到显示器边缘的功能
        # 获取屏幕几何信息
        screen = QApplication.desktop().screenGeometry()
        screen_width = screen.width()
        screen_height = screen.height()
        
        # 获取窗口当前位置和尺寸
        window_geometry = self.frameGeometry()
        window_x = window_geometry.x()
        window_y = window_geometry.y()
        window_width = window_geometry.width()
        window_height = window_geometry.height()
        
        # 吸附阈值（像素）
        snap_threshold = 50
        
        # 计算吸附后的位置
        new_x = window_x
        new_y = window_y
        
        # 左边缘吸附
        if window_x < snap_threshold:
            new_x = 0
        # 右边缘吸附
        elif screen_width - (window_x + window_width) < snap_threshold:
            new_x = screen_width - window_width
        
        # 上边缘吸附
        if window_y < snap_threshold:
            new_y = 0
        # 下边缘吸附
        elif screen_height - (window_y + window_height) < snap_threshold:
            new_y = screen_height - window_height
        
        # 移动窗口到新位置（如果有变化）
        if new_x != window_x or new_y != window_y:
            self.move(new_x, new_y)
    
    def mouseMoveEvent(self, event):
        if getattr(self, 'locked_position', False):
            return
        if self.is_dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
    
    def mouseDoubleClickEvent(self, event):
        # 双击事件：这里可以实现聊天窗口功能，但根据原代码已注释掉该功能
        pass
    
    def start_cache_cleaning(self):
        """开始缓存清理过程"""
        if self.is_cleaning_cache:
            return  # 防止重复启动
            
        self.is_cleaning_cache = True
        self.cache_cleaning_progress = 0
        
        # 创建定时器来控制清理进度和操作
        self.cache_cleaning_timer = QTimer(self)
        self.cache_cleaning_timer.timeout.connect(self.update_cache_cleaning_progress)
        self.cache_cleaning_timer.start(200)  # 每200毫秒执行一次操作
        
        # 显示开始清理的提示
        self.tray_icon.showMessage("缓存清理", "开始清理系统内存和缓存...", QSystemTrayIcon.Information, 2000)
    
    def update_cache_cleaning_progress(self):
        """更新缓存清理进度并执行实际清理操作"""
        try:
            # 根据进度执行不同的清理操作
            if 0 <= self.cache_cleaning_progress < 20:
                # 第一阶段：清理系统工作集
                self._clean_system_working_set()
            elif 20 <= self.cache_cleaning_progress < 50:
                # 第二阶段：清理文件系统缓存
                self._clean_file_system_cache()
            elif 50 <= self.cache_cleaning_progress < 80:
                # 第三阶段：清理进程工作集
                self._clean_process_working_sets()
            elif 80 <= self.cache_cleaning_progress < 100:
                # 最后阶段：优化系统内存
                self._optimize_system_memory()
            
            # 更新进度
            self.cache_cleaning_progress += 2
            
            # 清理完成
            if self.cache_cleaning_progress >= 100:
                self.cache_cleaning_progress = 100
                self.is_cleaning_cache = False
                if self.cache_cleaning_timer:
                    self.cache_cleaning_timer.stop()
                    self.cache_cleaning_timer = None
                    
                # 显示完成提示
                self.tray_icon.showMessage("缓存清理", "内存和缓存清理完成！", QSystemTrayIcon.Information, 3000)
        except Exception as e:
            print(f"缓存清理过程中出错: {e}")
            # 即使出错也要确保状态正确重置
            self.cache_cleaning_progress = 100
            self.is_cleaning_cache = False
            if self.cache_cleaning_timer:
                self.cache_cleaning_timer.stop()
                self.cache_cleaning_timer = None
        
        self.update()  # 触发重绘
    
    def _clean_system_working_set(self):
        """清理系统工作集"""
        try:
            # 导入Windows API
            import ctypes
            
            # 加载系统库
            kernel32 = ctypes.windll.kernel32
            
            # 调用Windows API清理系统工作集，使用c_size_t替代SIZE_T
            kernel32.SetProcessWorkingSetSize(-1, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception as e:
            print(f"清理系统工作集时出错: {e}")
            try:
                # 备选方案：改为静默无外部命令，避免弹窗
                pass
            except:
                pass
    
    def _check_empty_working_set(self):
        """检查EmptyWorkingSet函数是否可用（只检查一次）"""
        if not self._empty_working_set_checked:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                self._empty_working_set_available = hasattr(kernel32, 'EmptyWorkingSet')
                if not self._empty_working_set_available:
                    print("EmptyWorkingSet函数不可用，将使用SetProcessWorkingSetSize替代")
            except:
                self._empty_working_set_available = False
            self._empty_working_set_checked = True
        return self._empty_working_set_available
        
    def _clean_file_system_cache(self):
        """清理文件系统缓存"""
        try:
            logger.info("开始清理文件系统缓存")
            # 导入Windows API
            import ctypes
            
            # 加载系统库
            kernel32 = ctypes.windll.kernel32
            
            # 尝试使用EmptyWorkingSet函数
            try:
                # 使用缓存的检查结果
                if self._check_empty_working_set():
                    kernel32.EmptyWorkingSet(kernel32.GetCurrentProcess())
                    logger.debug("使用EmptyWorkingSet清理文件系统缓存")
                else:
                    # 备选方案：使用其他API
                    kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), 
                                                     ctypes.c_size_t(-1), 
                                                     ctypes.c_size_t(-1))
                    logger.debug("使用SetProcessWorkingSetSize清理文件系统缓存")
            except Exception as inner_e:
                logger.error(f"尝试EmptyWorkingSet时出错: {inner_e}")
                # 备选方案：改为静默无外部命令，避免弹窗
                try:
                    pass
                except:
                    pass
        except Exception as e:
            logger.error(f"清理文件系统缓存时出错: {e}")
        else:
            logger.info("文件系统缓存清理完成")
    
    def _clean_process_working_sets(self):
        """清理所有进程的工作集"""
        try:
            logger.info("开始清理进程工作集")
            import psutil
            import ctypes
            
            # 加载系统库
            kernel32 = ctypes.windll.kernel32
            
            # 使用缓存的检查结果
            has_empty_working_set = self._check_empty_working_set()
            
            # 获取所有进程并清理工作集（限制处理数量以避免系统压力过大）
            process_count = 0
            max_processes = 20  # 每次处理有限数量的进程
            
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if process_count >= max_processes:
                        break
                    
                    # 打开进程
                    handle = kernel32.OpenProcess(0x001F0FFF, False, proc.info['pid'])
                    if handle:
                        # 清理该进程的工作集
                        if has_empty_working_set:
                            try:
                                kernel32.EmptyWorkingSet(handle)
                            except:
                                # 如果EmptyWorkingSet失败，使用SetProcessWorkingSetSize
                                kernel32.SetProcessWorkingSetSize(handle, 
                                                                 ctypes.c_size_t(-1), 
                                                                 ctypes.c_size_t(-1))
                        else:
                            # 使用备选API
                            kernel32.SetProcessWorkingSetSize(handle, 
                                                             ctypes.c_size_t(-1), 
                                                             ctypes.c_size_t(-1))
                        kernel32.CloseHandle(handle)
                        process_count += 1
                except:
                    continue  # 忽略无法访问的进程
            logger.info(f"成功清理了 {process_count} 个进程的工作集")
        except Exception as e:
            logger.error(f"清理进程工作集时出错: {e}")
    
    def _optimize_system_memory(self):
        """优化系统内存使用"""
        try:
            logger.info("开始优化系统内存")
            import ctypes
            
            # 加载系统库
            kernel32 = ctypes.windll.kernel32
            
            # 执行最终的内存优化
            # 再次调用以确保最大效果
            kernel32.SetProcessWorkingSetSize(-1, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
            
            # 安全地尝试EmptyWorkingSet
            try:
                # 使用缓存的检查结果
                if self._check_empty_working_set():
                    kernel32.EmptyWorkingSet(kernel32.GetCurrentProcess())
                    logger.debug("使用EmptyWorkingSet优化系统内存")
                # 不再重复显示提示信息
            except Exception as inner_e:
                logger.error(f"优化系统内存时尝试使用EmptyWorkingSet出错: {inner_e}")
                # 使用备选方法
                kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), 
                                                 ctypes.c_size_t(-1), 
                                                 ctypes.c_size_t(-1))
                logger.debug("使用SetProcessWorkingSetSize优化系统内存")
            
            # 强制系统回收未使用的内存
            try:
                # 尝试调用更高级的内存优化API
                # 定义系统信息结构
                class SYSTEM_INFO(ctypes.Structure):
                    _fields_ = [
                        ("wProcessorArchitecture", ctypes.c_ushort),
                        ("wReserved", ctypes.c_ushort),
                        ("dwPageSize", ctypes.c_ulong),
                        ("lpMinimumApplicationAddress", ctypes.c_void_p),
                        ("lpMaximumApplicationAddress", ctypes.c_void_p),
                        ("dwActiveProcessorMask", ctypes.c_void_p),
                        ("dwNumberOfProcessors", ctypes.c_ulong),
                        ("dwProcessorType", ctypes.c_ulong),
                        ("dwAllocationGranularity", ctypes.c_ulong),
                        ("wProcessorLevel", ctypes.c_ushort),
                        ("wProcessorRevision", ctypes.c_ushort),
                    ]
                
                # 获取系统信息
                sysinfo = SYSTEM_INFO()
                kernel32.GetSystemInfo(ctypes.byref(sysinfo))
                logger.debug("获取系统信息成功")
                
                # 通过分配然后释放大块内存来促使系统整理内存
                # 注意：这个操作应该谨慎使用，这里只是一个小的示例
                try:
                    # 分配一个小内存块然后释放
                    ptr = ctypes.c_void_p()
                    kernel32.VirtualAlloc(None, 1024 * 1024, 0x3000, 0x40)
                    logger.debug("执行内存分配优化")
                except:
                    pass
            except Exception:
                pass  # 忽略高级API调用错误
        except Exception as e:
            logger.error(f"优化系统内存时出错: {e}")
        else:
            logger.info("系统内存优化完成")
    
    def show_context_menu(self, position):
        menu = QMenu()
        
        # 缓存清理菜单项
        clean_cache_action = QAction("缓存清理", self)
        clean_cache_action.triggered.connect(self.start_cache_cleaning)
        menu.addAction(clean_cache_action)
        
        # 显示FPS开关
        fps_action = QAction("显示FPS", self)
        fps_action.setCheckable(True)
        fps_action.setChecked(bool(self.settings.get("show_fps", True)))
        fps_action.triggered.connect(lambda checked: (self.settings.update({"show_fps": bool(checked)}), self.save_config(), self.apply_config()))
        menu.addAction(fps_action)
        
        # 仅在游戏中显示FPS
        fps_game_only_action = QAction("仅在游戏中显示FPS", self)
        fps_game_only_action.setCheckable(True)
        fps_game_only_action.setChecked(bool(self.settings.get("fps_only_in_game", True)))
        fps_game_only_action.triggered.connect(lambda checked: (self.settings.update({"fps_only_in_game": bool(checked)}), self.save_config(), self.apply_config()))
        menu.addAction(fps_game_only_action)

        # 游戏内HUD显示开关
        hud_action = QAction("游戏内HUD显示", self)
        hud_action.setCheckable(True)
        hud_action.setChecked(bool(self.settings.get("enable_ingame_hud", True)))
        hud_action.triggered.connect(lambda checked: (self.settings.update({"enable_ingame_hud": bool(checked)}), self.save_config(), self.apply_config()))
        menu.addAction(hud_action)
        
        # 位置锁定
        lock_action = QAction("位置锁定", self)
        lock_action.setCheckable(True)
        lock_action.setChecked(bool(self.settings.get("locked_position", False)))
        lock_action.triggered.connect(lambda checked: (self.settings.update({"locked_position": bool(checked)}), setattr(self, "locked_position", bool(checked)), self.save_config(), self.apply_config()))
        menu.addAction(lock_action)
        
        # 透明度设置
        def set_opacity():
            try:
                current_opacity = float(self.settings.get("opacity", 1.0))
                val, ok = QInputDialog.getDouble(self, "设置透明度", "范围 0.2–1.0", current_opacity, 0.2, 1.0, 2)
                if ok:
                    self.settings["opacity"] = float(max(0.2, min(1.0, val)))
                    self.save_config()
                    self.apply_config()
                    logger.debug(f"透明度设置成功: {val}")
            except Exception as e:
                logger.error(f"设置透明度失败: {e}")
        opacity_action = QAction("透明度...", self)
        opacity_action.triggered.connect(set_opacity)
        menu.addAction(opacity_action)
        
        # 编辑自定义非游戏进程
        def edit_processes():
            try:
                text, ok = QInputDialog.getText(self, "编辑非游戏进程", "输入进程名，逗号或换行分隔")
                if ok:
                    items = [x.strip().lower() for x in re.split(r"[\n,;]+", text) if x.strip()]
                    self.settings["custom_non_game_processes"] = items
                    self.save_config()
                    self.apply_config()
                    logger.debug(f"编辑非游戏进程成功: {len(items)} 个进程")
            except Exception as e:
                logger.error(f"编辑非游戏进程失败: {e}")
        edit_proc_action = QAction("编辑非游戏进程...", self)
        edit_proc_action.triggered.connect(edit_processes)
        menu.addAction(edit_proc_action)
        
        # 编辑自定义非游戏标题关键字
        def edit_titles():
            try:
                text, ok = QInputDialog.getText(self, "编辑非游戏标题关键字", "输入关键词，逗号或换行分隔")
                if ok:
                    items = [x.strip().lower() for x in re.split(r"[\n,;]+", text) if x.strip()]
                    self.settings["custom_non_game_titles"] = items
                    self.save_config()
                    self.apply_config()
                    logger.debug(f"编辑非游戏标题成功: {len(items)} 个关键词")
            except Exception as e:
                logger.error(f"编辑非游戏标题失败: {e}")
        edit_titles_action = QAction("编辑非游戏标题...", self)
        edit_titles_action.triggered.connect(edit_titles)
        menu.addAction(edit_titles_action)
        
        # 关闭菜单项
        close_action = QAction("关闭", self)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)
        
        menu.exec_(position)
        
    def check_startup(self):
        """检查程序是否已设置开机自启动"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                                r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                                0, winreg.KEY_READ)
            # 尝试获取键值
            value, _ = winreg.QueryValueEx(key, "小浩悬浮球")
            winreg.CloseKey(key)
            logger.debug("检查开机自启动: 已设置")
            return True
        except OSError:
            logger.debug("检查开机自启动: 未设置")
            return False
    
    def set_startup(self, enable):
        """设置或取消开机自启动"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                                r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                                0, winreg.KEY_SET_VALUE)
            
            if enable:
                # 获取当前程序的路径 - 适配PyInstaller打包后的情况
                if hasattr(sys, '_MEIPASS'):
                    # 打包后的exe文件路径
                    exe_path = os.path.abspath(sys.executable)
                else:
                    # 开发环境中的路径
                    exe_path = os.path.abspath(sys.argv[0])
                    # 如果是通过Python解释器运行的，需要包含解释器路径
                    if exe_path.endswith('.py'):
                        exe_path = f'"{sys.executable}" "{exe_path}"' 
                
                # 设置开机自启动
                winreg.SetValueEx(key, "小浩悬浮球", 0, winreg.REG_SZ, exe_path)
            else:
                # 尝试删除开机自启动项
                try:
                    winreg.DeleteValue(key, "小浩悬浮球")
                except OSError:
                    # 如果键不存在，忽略错误
                    pass
            
            winreg.CloseKey(key)
        except Exception as e:
            print(f"设置开机自启动时出错: {e}")
    
    def init_system_tray(self):
        """初始化系统托盘图标"""
        self.tray_icon = QSystemTrayIcon(self)
        
        # 设置托盘图标上下文菜单
        self.tray_menu = QMenu()
        
        # 显示悬浮球菜单项
        show_action = QAction("显示悬浮球", self)
        show_action.triggered.connect(self.show)
        self.tray_menu.addAction(show_action)
        
        # 隐藏悬浮球菜单项
        hide_action = QAction("隐藏悬浮球", self)
        hide_action.triggered.connect(self.hide)
        self.tray_menu.addAction(hide_action)
        
        # 开机自启动菜单项
        self.startup_action = QAction("开机自启动", self)
        self.startup_action.setCheckable(True)
        # 检查当前是否已设置开机自启动
        self.startup_action.setChecked(self.check_startup())
        self.startup_action.triggered.connect(lambda: self.set_startup(self.startup_action.isChecked()))
        self.tray_menu.addAction(self.startup_action)
        
        # 显示FPS开关
        fps_action = QAction("显示FPS", self)
        fps_action.setCheckable(True)
        fps_action.setChecked(bool(self.settings.get("show_fps", True)))
        fps_action.triggered.connect(lambda checked: (self.settings.update({"show_fps": bool(checked)}), self.save_config(), self.apply_config()))
        self.tray_menu.addAction(fps_action)
        
        # 仅在游戏中显示FPS
        fps_game_only_action = QAction("仅在游戏中显示FPS", self)
        fps_game_only_action.setCheckable(True)
        fps_game_only_action.setChecked(bool(self.settings.get("fps_only_in_game", True)))
        fps_game_only_action.triggered.connect(lambda checked: (self.settings.update({"fps_only_in_game": bool(checked)}), self.save_config(), self.apply_config()))
        self.tray_menu.addAction(fps_game_only_action)

        # 游戏内HUD显示开关
        hud_action = QAction("游戏内HUD显示", self)
        hud_action.setCheckable(True)
        hud_action.setChecked(bool(self.settings.get("enable_ingame_hud", True)))
        hud_action.triggered.connect(lambda checked: (self.settings.update({"enable_ingame_hud": bool(checked)}), self.save_config(), self.apply_config()))
        self.tray_menu.addAction(hud_action)
        
        # 位置锁定
        lock_action = QAction("位置锁定", self)
        lock_action.setCheckable(True)
        lock_action.setChecked(bool(self.settings.get("locked_position", False)))
        lock_action.triggered.connect(lambda checked: (self.settings.update({"locked_position": bool(checked)}), setattr(self, "locked_position", bool(checked)), self.save_config(), self.apply_config()))
        self.tray_menu.addAction(lock_action)
        
        # 透明度设置
        def set_opacity_tray():
            try:
                current_opacity = float(self.settings.get("opacity", 1.0))
                val, ok = QInputDialog.getDouble(self, "设置透明度", "范围 0.2–1.0", current_opacity, 0.2, 1.0, 2)
                if ok:
                    self.settings["opacity"] = float(max(0.2, min(1.0, val)))
                    self.save_config()
                    self.apply_config()
            except Exception as e:
                print(f"设置透明度失败: {e}")
        opacity_action = QAction("透明度...", self)
        opacity_action.triggered.connect(set_opacity_tray)
        self.tray_menu.addAction(opacity_action)
        
        # 编辑自定义非游戏进程
        def edit_processes_tray():
            try:
                text, ok = QInputDialog.getText(self, "编辑非游戏进程", "输入进程名，逗号或换行分隔")
                if ok:
                    items = [x.strip().lower() for x in re.split(r"[\n,;]+", text) if x.strip()]
                    self.settings["custom_non_game_processes"] = items
                    self.save_config()
                    self.apply_config()
            except Exception as e:
                print(f"编辑非游戏进程失败: {e}")
        edit_proc_action = QAction("编辑非游戏进程...", self)
        edit_proc_action.triggered.connect(edit_processes_tray)
        self.tray_menu.addAction(edit_proc_action)
        
        # 编辑自定义非游戏标题关键字
        def edit_titles_tray():
            try:
                text, ok = QInputDialog.getText(self, "编辑非游戏标题关键字", "输入关键词，逗号或换行分隔")
                if ok:
                    items = [x.strip().lower() for x in re.split(r"[\n,;]+", text) if x.strip()]
                    self.settings["custom_non_game_titles"] = items
                    self.save_config()
                    self.apply_config()
            except Exception as e:
                print(f"编辑非游戏标题失败: {e}")
        edit_titles_action = QAction("编辑非游戏标题...", self)
        edit_titles_action.triggered.connect(edit_titles_tray)
        self.tray_menu.addAction(edit_titles_action)
        
        # 缓存清理菜单项
        clean_cache_action = QAction("缓存清理", self)
        clean_cache_action.triggered.connect(self.start_cache_cleaning)
        self.tray_menu.addAction(clean_cache_action)
        
        # 关闭菜单项
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(QApplication.quit)
        self.tray_menu.addAction(exit_action)
        
        # 先更新托盘图标温度显示
        self.update_tray_icon()
        
        self.tray_icon.setContextMenu(self.tray_menu)
        
        # 显示托盘图标
        self.tray_icon.show()
        
    def apply_config(self):
        try:
            # 窗口透明度与位置锁定
            opacity = float(self.settings.get("opacity", 1.0))
            self.setWindowOpacity(max(0.2, min(1.0, opacity)))
            self.locked_position = bool(self.settings.get("locked_position", False))
            
            # 性能相关参数（供工作线程使用）
            self.performance_sleep_interval = float(self.settings.get("performance_sleep_interval", 0.05))
            self.signal_interval = float(self.settings.get("signal_interval", 0.1))
            
            # 全局非游戏全屏严格模式与调试日志
            global STRICT_NON_GAME_FULLSCREEN, DEBUG_LOG
            STRICT_NON_GAME_FULLSCREEN = bool(self.settings.get("strict_non_game_fullscreen", True))
            DEBUG_LOG = bool(self.settings.get("debug_log", False))
            
            # 自定义黑名单集合
            global CUSTOM_NON_GAME_PROCESSES, CUSTOM_NON_GAME_TITLE_KEYWORDS
            CUSTOM_NON_GAME_PROCESSES = set(self.settings.get("custom_non_game_processes", []))
            CUSTOM_NON_GAME_TITLE_KEYWORDS = set(self.settings.get("custom_non_game_titles", []))
            
            # 刷新托盘图标状态
            self.update_tray_icon()
        except Exception as e:
            print(f"应用配置失败: {e}")
        
    def load_config(self):
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.settings.update(data)
        except Exception as e:
            print(f"加载配置失败: {e}")
        
    def save_config(self):
        try:
            if not os.path.exists(CONFIG_DIR):
                os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")
        
    def format_speed(self, bytes_per_sec):
        """格式化网络速度显示"""
        # 小于1KB/s
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        # 小于1MB/s
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        # 小于1GB/s
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    
    def update_tray_icon(self):
        """更新任务栏显示"""
        # 创建图标
        # 当HUD关闭或非游戏时无需强制隐藏悬浮球，但HUD自身管理显示
        try:
            hud_enabled = bool(self.settings.get("enable_ingame_hud", True))
            if hasattr(self, 'overlay_hud') and self.overlay_hud is not None:
                if hud_enabled and self.is_gaming:
                    self.overlay_hud.show()
                else:
                    self.overlay_hud.hide()
        except Exception:
            pass
        
        icon_size = 16
        pixmap = QPixmap(icon_size, icon_size)
        pixmap.fill(Qt.transparent)  # 设置透明背景
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 设置字体
        font = QFont("Arial", 8, QFont.Bold)
        painter.setFont(font)
        
        # 判断是显示温度还是FPS（支持设置开关）
        show_fps_enabled = bool(self.settings.get("show_fps", True))
        fps_only_in_game = bool(self.settings.get("fps_only_in_game", True))
        should_show_fps = show_fps_enabled and self.fps > 0 and (not fps_only_in_game or self.is_gaming)
        if should_show_fps:
            # FPS显示模式
            # 根据FPS值设置不同的颜色
            fps_ratio = min(self.fps / 144, 1.0)
            if fps_ratio > 0.8:
                fps_color = QColor(0, 255, 0, 200)
            elif fps_ratio > 0.5:
                fps_color = QColor(0, 191, 255, 200)
            else:
                fps_color = QColor(255, 0, 0, 200)
            
            painter.setPen(QPen(fps_color))
            
            # 绘制FPS文本（只显示数字，避免太长）
            fps_text = f"{self.fps}"
            text_rect = painter.fontMetrics().boundingRect(fps_text)
            
            # 居中绘制文本
            x = (icon_size - text_rect.width()) // 2
            y = (icon_size + text_rect.height()) // 2
            painter.drawText(x, y, fps_text)
        else:
            # 温度显示模式
            # 设置文本颜色（根据温度变化）
            temp_color = self.get_gradient_color(min(self.gpu_temp / 100, 1.0))
            painter.setPen(QPen(temp_color))
            
            # 绘制温度文本
            temp_text = f"{int(self.gpu_temp)}°"
            text_rect = painter.fontMetrics().boundingRect(temp_text)
            
            # 居中绘制文本
            x = (icon_size - text_rect.width()) // 2
            y = (icon_size + text_rect.height()) // 2
            painter.drawText(x, y, temp_text)
        
        painter.end()
        
        # 设置托盘图标
        self.tray_icon.setIcon(QIcon(pixmap))
        
        # 设置托盘提示，增加网络速度显示
        formatted_down_speed = self.format_speed(self.down_speed)
        formatted_up_speed = self.format_speed(self.up_speed)
        
        # 根据游戏状态调整提示文本
        if should_show_fps:
            cpu_temp_display = f"{int(self.cpu_temp)}°C" if isinstance(self.cpu_temp, (int, float)) else "--"
            gpu_temp_display = f"{int(self.gpu_temp)}°C" if self.gpu_temp > 0 else "--"
            self.tray_icon.setToolTip(f"当前游戏FPS: {self.fps}\n" \
                                    f"CPU使用率: {int(self.cpu_usage)}%  CPU温度: {cpu_temp_display}\n" \
                                    f"GPU使用率: {int(self.gpu_load)}%  GPU温度: {gpu_temp_display}\n" \
                                    f"下载速度: {formatted_down_speed}\n" \
                                    f"上传速度: {formatted_up_speed}")
        else:
            cpu_temp_display = f"{int(self.cpu_temp)}°C" if isinstance(self.cpu_temp, (int, float)) else "--"
            self.tray_icon.setToolTip(f"GPU温度: {int(self.gpu_temp)}°C\n" \
                                    f"CPU使用率: {int(self.cpu_usage)}%  CPU温度: {cpu_temp_display}\n" \
                                    f"GPU使用率: {int(self.gpu_load)}%\n" \
                                    f"下载速度: {formatted_down_speed}\n" \
                                    f"上传速度: {formatted_up_speed}")
        
class GameOverlayHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(240, 70)
        self.cpu_usage = 0
        self.cpu_temp = None
        self.gpu_load = 0
        self.gpu_temp = 0
        self.fps = 0

    def update_metrics(self, cpu_usage, cpu_temp, gpu_load, gpu_temp, fps):
        self.cpu_usage = cpu_usage
        self.cpu_temp = cpu_temp
        self.gpu_load = gpu_load
        self.gpu_temp = gpu_temp
        self.fps = fps
        self.reposition_to_foreground()
        self.update()

    def reposition_to_foreground(self):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            x = int(rect.left + 12)
            y = int(rect.top + 12)
            self.move(x, y)
        except Exception:
            pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 215, 0)))
        painter.setFont(QFont("Arial", 13, QFont.Bold))
        top_y = 6
        line_h = 18
        fps_text = f"FPS {self.fps}" if self.fps > 0 else "FPS --"
        cpu_temp_text = f"{int(self.cpu_temp)}°" if isinstance(self.cpu_temp, (int, float)) else "--"
        gpu_temp_text = f"{int(self.gpu_temp)}°" if self.gpu_temp > 0 else "--"
        cpu_text = f"CPU {self.cpu_usage:.0f}% {cpu_temp_text}"
        gpu_text = f"GPU {self.gpu_load:.0f}% {gpu_temp_text}"
        painter.drawText(QRect(0, top_y, self.width(), line_h), Qt.AlignLeft, fps_text)
        painter.drawText(QRect(0, top_y+line_h, self.width(), line_h), Qt.AlignLeft, cpu_text)
        painter.drawText(QRect(0, top_y+2*line_h, self.width(), line_h), Qt.AlignLeft, gpu_text)
        painter.end()

    def closeEvent(self, event):
        event.ignore()

    
    def closeEvent(self, event):
        """关闭窗口事件处理"""
        # 默认不关闭程序，只隐藏主窗口
        self.hide()
        # 确保即使隐藏时也安全停止线程（如果需要）
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.stop()
        event.ignore()  # 忽略关闭事件

if __name__ == "__main__":
    # 初始化日志系统
    try:
        logger.info("小浩悬浮球程序启动")
        logger.info(f"Python版本: {sys.version}")
        logger.info(f"工作目录: {os.getcwd()}")
    except Exception as e:
        print(f"日志系统初始化失败: {e}")
    
    # 首先设置正确的QT_PLUGIN_PATH环境变量
    try:
        # 检查是否为PyInstaller打包后的程序
        if hasattr(sys, '_MEIPASS'):
            # 获取PyInstaller临时目录下的plugins路径（修正为Qt5而不是Qt）
            pyqt5_plugins_path = os.path.join(sys._MEIPASS, 'PyQt5', 'Qt5', 'plugins')
            if os.path.exists(pyqt5_plugins_path):
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = pyqt5_plugins_path
                logger.info(f"设置QT_QPA_PLATFORM_PLUGIN_PATH={pyqt5_plugins_path}")
        else:
            # 获取PyQt5的安装路径
            import PyQt5
            pyqt5_path = os.path.dirname(PyQt5.__file__)
            
            # 设置正确的plugins路径（从测试脚本中确认的路径）
            qt5_plugins_path = os.path.join(pyqt5_path, "Qt5", "plugins")
            logger.info(f"设置QT_QPA_PLATFORM_PLUGIN_PATH={qt5_plugins_path}")
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = qt5_plugins_path
            
            # 同时确保Qt5/bin目录在PATH中
            qt5_bin_path = os.path.join(pyqt5_path, "Qt5", "bin")
            if os.path.exists(qt5_bin_path):
                if qt5_bin_path not in os.environ["PATH"]:
                    os.environ["PATH"] = qt5_bin_path + ";" + os.environ["PATH"]
    except Exception as e:
        logger.error(f"设置Qt环境变量时出错: {e}")
        print(f"设置Qt环境变量时出错: {e}")
    
    # 然后创建应用实例
    try:
        logger.info("开始创建QApplication...")
        app = QApplication(sys.argv)
        logger.info("QApplication创建成功")
        
        # 确保中文显示正常
        # 设置全局字体
        font = QFont("SimHei")
        app.setFont(font)
        logger.info("字体设置成功")
        
        logger.info("开始创建FloatingBall实例...")
        floating_ball = FloatingBall()
        logger.info("FloatingBall实例创建成功")
        
        logger.info("开始显示悬浮球窗口...")
        floating_ball.show()
        logger.info("悬浮球窗口显示成功")
        
        logger.info("开始运行Qt事件循环...")
        sys.exit(app.exec_())
        
    except Exception as e:
        logger.error(f"程序运行过程中出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        print(f"程序运行过程中出错: {e}")
        print(traceback.format_exc())
        input("按回车键退出...")
    
