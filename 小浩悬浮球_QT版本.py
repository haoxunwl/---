# _*_ coding : utf-8 _*_
# !/usr/bin/env python3

import os
import sys
import time
import winreg  # 用于访问Windows注册表
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
    pyqtSignal, QObject
)
import psutil
import GPUtil

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

# 工作线程类，用于获取系统信息
class SystemInfoWorker(QObject):
    update_signal = pyqtSignal(float, float, float, float, float)  # CPU使用率, GPU温度, GPU使用率, 下载速度, 上传速度
    error_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        # 初始化网络统计数据
        self.last_net_io = psutil.net_io_counters()
        self.last_time = time.time()
    
    def run(self):
        try:
            while True:
                # 获取CPU使用率
                cpu_usage = psutil.cpu_percent(interval=1, percpu=True)
                avg_cpu_usage = sum(cpu_usage) / len(cpu_usage) if isinstance(cpu_usage, list) else cpu_usage
                
                # 获取GPU信息
                gpus = GPUtil.getGPUs()
                gpu_temp = gpus[0].temperature if gpus else 0
                gpu_load = gpus[0].load * 100 if gpus else 0
                
                # 获取网络速度
                current_net_io = psutil.net_io_counters()
                current_time = time.time()
                time_diff = current_time - self.last_time
                
                # 计算下载速度和上传速度（字节/秒）
                down_speed = (current_net_io.bytes_recv - self.last_net_io.bytes_recv) / time_diff
                up_speed = (current_net_io.bytes_sent - self.last_net_io.bytes_sent) / time_diff
                
                # 更新网络统计数据
                self.last_net_io = current_net_io
                self.last_time = current_time
                
                # 发送更新信号
                self.update_signal.emit(avg_cpu_usage, gpu_temp, gpu_load, down_speed, up_speed)
                
                # 每秒更新一次
                time.sleep(1)
        except Exception as e:
            self.error_signal.emit(str(e))

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
        self.down_speed = 0  # 下载速度（字节/秒）
        self.up_speed = 0  # 上传速度（字节/秒）
        self.is_dragging = False
        self.drag_position = QPoint()
        
        # 缓存清理相关变量
        self.is_cleaning_cache = False
        self.cache_cleaning_progress = 0
        self.cache_cleaning_timer = None
        
        # 初始化UI
        self.init_ui()
        
        # 初始化系统托盘图标
        self.init_system_tray()
        
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
        # 创建工作线程
        self.worker = SystemInfoWorker()
        self.worker_thread = QRunnable.create(self.worker.run)
        self.worker_thread.setAutoDelete(True)
        
        # 连接信号和槽
        self.worker.update_signal.connect(self.update_system_info)
        self.worker.error_signal.connect(self.handle_error)
        
        # 启动线程
        QThreadPool.globalInstance().start(self.worker_thread)
        
    def update_system_info(self, cpu_usage, gpu_temp, gpu_load, down_speed, up_speed):
        self.prev_cpu_usage = self.cpu_usage
        self.prev_gpu_load = self.gpu_load
        self.cpu_usage = cpu_usage
        self.gpu_temp = gpu_temp
        self.gpu_load = gpu_load
        self.down_speed = down_speed
        self.up_speed = up_speed
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
        
        # 判断是显示温度还是缓存清理进度
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
        
        # 设置笔的宽度和样式
        pen = QPen(color, 5, Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)  # 圆润的端点样式
        painter.setPen(pen)
        
        # 直接根据比例绘制进度圆环
        current_ratio = self.cache_cleaning_progress / 100.0 if self.is_cleaning_cache else min(self.gpu_temp / 100.0, 1.0)
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
    
    def draw_system_info(self, painter):
        # 判断是显示温度还是缓存清理进度
        if self.is_cleaning_cache:
            # 缓存清理模式
            # 绘制缓存清理进度
            cache_color = QColor(0, 100, 255)
            painter.setPen(QPen(cache_color))
            painter.setFont(QFont("Arial", 10, QFont.Bold))
            painter.drawText(QRect(0, 70, self.window_width, 20),
                            Qt.AlignCenter, "清理中")
            
            # 绘制进度百分比
            painter.setFont(QFont("Arial", 11, QFont.Bold))
            painter.drawText(QRect(0, 90, self.window_width, 20),
                            Qt.AlignCenter, f"{self.cache_cleaning_progress}%")
        else:
            # 温度显示模式
            # 绘制温度显示
            temp_color = self.get_gradient_color(min(self.gpu_temp / 100, 1.0))
            painter.setPen(QPen(temp_color))
            painter.setFont(QFont("Arial", 13, QFont.Bold))
            painter.drawText(QRect(0, 80, self.window_width, 20),
                            Qt.AlignCenter, f"{int(self.gpu_temp)}°C")
        
        # 始终显示CPU和GPU使用率信息
        # 计算CPU和GPU的变化箭头
        cpu_arrow = "↑" if self.cpu_usage > self.prev_cpu_usage else "↓" if self.cpu_usage < self.prev_cpu_usage else "-"
        gpu_arrow = "↑" if self.gpu_load > self.prev_gpu_load else "↓" if self.gpu_load < self.prev_gpu_load else "-"
        
        # 绘制CPU使用率
        cpu_color = self.get_usage_color(self.cpu_usage)
        painter.setPen(QPen(cpu_color))
        painter.setFont(QFont("Helvetica", 11, QFont.Bold))
        painter.drawText(QRect(0, 130, self.window_width, 20),
                        Qt.AlignCenter, f"{cpu_arrow}{self.cpu_usage:.1f}%")
        
        # 绘制GPU使用率
        gpu_color = self.get_usage_color(self.gpu_load)
        painter.setPen(QPen(gpu_color))
        painter.setFont(QFont("Arial", 11, QFont.Bold))
        painter.drawText(QRect(0, 165, self.window_width, 20),
                        Qt.AlignCenter, f"{gpu_arrow}{self.gpu_load:.1f}%")
    
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
        if self.is_dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
    
    def mouseDoubleClickEvent(self, event):
        # 双击事件：这里可以实现聊天窗口功能，但根据原代码已注释掉该功能
        pass
    
    def start_cache_cleaning(self):
        """开始缓存清理过程"""
        self.is_cleaning_cache = True
        self.cache_cleaning_progress = 0
        
        # 创建定时器来模拟缓存清理进度
        self.cache_cleaning_timer = QTimer(self)
        self.cache_cleaning_timer.timeout.connect(self.update_cache_cleaning_progress)
        self.cache_cleaning_timer.start(100)  # 每100毫秒更新一次
    
    def update_cache_cleaning_progress(self):
        """更新缓存清理进度"""
        self.cache_cleaning_progress += 1
        
        # 模拟缓存清理完成
        if self.cache_cleaning_progress >= 100:
            self.cache_cleaning_progress = 100
            self.is_cleaning_cache = False
            if self.cache_cleaning_timer:
                self.cache_cleaning_timer.stop()
                self.cache_cleaning_timer = None
        
        self.update()  # 触发重绘
    
    def show_context_menu(self, position):
        menu = QMenu()
        
        # 缓存清理菜单项
        clean_cache_action = QAction("缓存清理", self)
        clean_cache_action.triggered.connect(self.start_cache_cleaning)
        menu.addAction(clean_cache_action)
        
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
            return True
        except OSError:
            return False
    
    def set_startup(self, enable):
        """设置或取消开机自启动"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                                r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                                0, winreg.KEY_SET_VALUE)
            
            if enable:
                # 获取当前程序的路径
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
        # 创建一个显示温度的图标
        icon_size = 16
        pixmap = QPixmap(icon_size, icon_size)
        pixmap.fill(Qt.transparent)  # 设置透明背景
        
        # 在图标上绘制温度
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 设置字体
        font = QFont("Arial", 8, QFont.Bold)
        painter.setFont(font)
        
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
        self.tray_icon.setToolTip(f"GPU温度: {int(self.gpu_temp)}°C\n" \
                                f"CPU使用率: {int(self.cpu_usage)}%\n" \
                                f"GPU使用率: {int(self.gpu_load)}%\n" \
                                f"下载速度: {formatted_down_speed}\n" \
                                f"上传速度: {formatted_up_speed}")
        
    def closeEvent(self, event):
        """关闭窗口事件处理"""
        # 默认不关闭程序，只隐藏主窗口
        self.hide()
        event.ignore()  # 忽略关闭事件

if __name__ == "__main__":
    # 导入必要的模块
    import os
    import sys
    
    # 首先设置正确的QT_PLUGIN_PATH环境变量
    try:
        # 获取PyQt5的安装路径
        import PyQt5
        pyqt5_path = os.path.dirname(PyQt5.__file__)
        
        # 设置正确的plugins路径（从测试脚本中确认的路径）
        qt5_plugins_path = os.path.join(pyqt5_path, "Qt5", "plugins")
        print(f"设置QT_PLUGIN_PATH={qt5_plugins_path}")
        os.environ["QT_PLUGIN_PATH"] = qt5_plugins_path
        
        # 同时确保Qt5/bin目录在PATH中
        qt5_bin_path = os.path.join(pyqt5_path, "Qt5", "bin")
        if os.path.exists(qt5_bin_path):
            if qt5_bin_path not in os.environ["PATH"]:
                os.environ["PATH"] = qt5_bin_path + ";" + os.environ["PATH"]
    except Exception as e:
        print(f"设置Qt环境变量时出错: {e}")
    
    # 然后创建应用实例
    app = QApplication(sys.argv)
    
    # 确保中文显示正常
    font_db = QFontDatabase()
    
    # 设置全局字体
    font = QFont("SimHei")
    app.setFont(font)
    
    floating_ball = FloatingBall()
    floating_ball.show()
    
    sys.exit(app.exec_())