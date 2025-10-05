import json
import logging
import sys
import numpy as np
import pyaudio
import os
import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                             QWidget, QLabel, QProgressBar,
                             QSlider, QStatusBar, QPushButton, QDialog,
                             QTextEdit, QHBoxLayout, QMessageBox)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont, QPainter, QLinearGradient, QColor, QPen, QPainterPath
from loguru import logger
import traceback


class VolumeProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(30)

    def paintEvent(self, event):
        """进度条绘制重写"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.0, QColor(0, 255, 0))
        gradient.setColorAt(0.6, QColor(255, 255, 0))
        gradient.setColorAt(0.8, QColor(255, 165, 0))
        gradient.setColorAt(1.0, QColor(255, 0, 0))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(240, 240, 240))
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 5, 5)

        progress_width = int(self.width() * self.value() / 100)
        if progress_width > 0:
            painter.setBrush(gradient)
            painter.drawRoundedRect(0, 0, progress_width, self.height(), 5, 5)

        painter.setPen(QColor(180, 180, 180))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 5, 5)

        painter.end()


class WaveformWidget(QWidget):
    """波形显示部件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)
        self.waveform_data = []
        self.max_data_points = 200

    def add_data_point(self, level):
        """添加新的数据点"""
        # 转换为0-1之间的值
        normalized_level = level / 100.0

        # 添加到波形数据
        self.waveform_data.append(normalized_level)

        # 保持数据点数不超过最大值
        if len(self.waveform_data) > self.max_data_points:
            self.waveform_data.pop(0)

        # 更新显示
        self.update()

    def paintEvent(self, event):
        """绘制波形"""
        if not self.waveform_data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 设置背景
        painter.fillRect(event.rect(), QColor(240, 240, 240))

        # 绘制网格线
        painter.setPen(QColor(200, 200, 200))
        for i in range(1, 4):
            y = self.height() * i / 4
            painter.drawLine(0, y, self.width(), y)

        # 绘制波形
        if len(self.waveform_data) > 1:
            # 计算每个数据点的x坐标
            x_step = self.width() / (len(self.waveform_data) - 1)

            # 创建波形路径
            path = QPainterPath()
            path.moveTo(0, self.height() * (1 - self.waveform_data[0]))

            for i in range(1, len(self.waveform_data)):
                x = i * x_step
                y = self.height() * (1 - self.waveform_data[i])
                path.lineTo(x, y)

            # 绘制波形线
            painter.setPen(QPen(QColor(41, 128, 185), 2))
            painter.drawPath(path)

            # 填充波形下方区域
            path.lineTo(self.width(), self.height())
            path.lineTo(0, self.height())
            path.closeSubpath()

            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0, QColor(41, 128, 185, 100))
            gradient.setColorAt(1, QColor(41, 128, 185, 30))

            painter.fillPath(path, gradient)

        # 绘制标题
        painter.setPen(QColor(100, 100, 100))
        painter.drawText(10, 15, "音量波形图")

        painter.end()


class ReportDialog(QDialog):
    """报告显示对话框"""

    def __init__(self, report_data_text, parent=None):
        super().__init__(parent)
        self.report_data_text = report_data_text
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("ClassVoiceMonitor - Report")
        self.setFixedSize(600, 700)

        layout = QVBoxLayout(self)

        # title_label = QLabel("监测报告")
        # title_font = QFont("Microsoft YaHei", 18, QFont.Bold)
        # title_label.setFont(title_font)
        # title_label.setAlignment(Qt.AlignCenter)
        # layout.addWidget(title_label)

        report_text = QTextEdit()
        report_text.setFont(QFont("Microsoft YaHei", 10))
        report_text.setReadOnly(True)
        report_text.setText(self.report_data_text)
        layout.addWidget(report_text)

        button_layout = QHBoxLayout()
        close_button = QPushButton("保存并关闭")
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()

        self.version = "1.0.0"
        self.config_version = "1.0.0"

        self.audio_level_history = []  # 用于平滑音频级别
        self.history_size = 5  # 平滑窗口大小
        self.max_rms = 10000  # 初始灵敏度值

        # 得分系统变量
        self.score = 0
        self.combo_count = 0
        self.combo_start_time = 0
        self.last_level = 0
        self.rating_timer_count = 0
        self.current_rating = ""
        self.rating_bonus = 0
        self.rating_display_time = 0

        # 记录系统变量
        self.is_recording = False
        self.start_time = None
        self.end_time = None
        self.rating_history = []
        self.combo_history = []

        self.init_ui()
        self.read_config()

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("ClassVoiceMonitor")
        self.setMinimumSize(500, 500)

        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        # 标题标签
        title_label = QLabel("ClassVoiceMonitor")
        title_font = QFont("Microsoft YaHei", 16, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # 音量级别显示
        self.level_label = QLabel("音量级别: 0.0%")
        level_font = QFont("Microsoft YaHei", 18, QFont.Bold)
        self.level_label.setFont(level_font)
        self.level_label.setAlignment(Qt.AlignCenter)
        self.level_label.setStyleSheet("""
            QLabel {
                background-color: #2c3e50;
                color: #ecf0f1;
                border-radius: 10px;
                padding: 15px;
            }
        """)
        layout.addWidget(self.level_label)

        # 得分显示
        self.score_label = QLabel("得分: 0")
        score_font = QFont("Microsoft YaHei", 16, QFont.Bold)
        self.score_label.setFont(score_font)
        self.score_label.setAlignment(Qt.AlignCenter)
        self.score_label.setStyleSheet("""
            QLabel {
                background-color: #34495e;
                color: #ecf0f1;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        layout.addWidget(self.score_label)

        # 评级和连击显示
        self.rating_label = QLabel("")
        rating_font = QFont("Microsoft YaHei", 14, QFont.Bold)
        self.rating_label.setFont(rating_font)
        self.rating_label.setAlignment(Qt.AlignCenter)
        self.rating_label.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                padding: 5px;
            }
        """)
        layout.addWidget(self.rating_label)

        self.combo_label = QLabel("")
        combo_font = QFont("Microsoft YaHei", 14, QFont.Bold)
        self.combo_label.setFont(combo_font)
        self.combo_label.setAlignment(Qt.AlignCenter)
        self.combo_label.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                padding: 5px;
            }
        """)
        layout.addWidget(self.combo_label)

        # 自定义音量进度条
        self.progress_bar = VolumeProgressBar()
        layout.addWidget(self.progress_bar)

        # 灵敏度校准滑块
        sensitivity_layout = QHBoxLayout()
        sensitivity_label = QLabel("Max RMS:")
        sensitivity_label.setFont(QFont("Microsoft YaHei", 10))
        sensitivity_layout.addWidget(sensitivity_label)

        self.sensitivity_slider = QSlider(Qt.Horizontal)
        self.sensitivity_slider.setRange(1000, 30000)
        self.sensitivity_slider.setValue(self.max_rms)
        self.sensitivity_slider.setTickPosition(QSlider.TicksBelow)
        self.sensitivity_slider.setTickInterval(5000)
        self.sensitivity_slider.valueChanged.connect(self.update_sensitivity)
        sensitivity_layout.addWidget(self.sensitivity_slider)

        self.sensitivity_value_label = QLabel(f"{self.max_rms}")
        self.sensitivity_value_label.setFont(QFont("Microsoft YaHei", 10))
        self.sensitivity_value_label.setFixedWidth(60)
        sensitivity_layout.addWidget(self.sensitivity_value_label)

        layout.addLayout(sensitivity_layout)

        # 控制按钮
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("开始监测")
        self.start_button.setFont(QFont("Microsoft YaHei", 12))
        self.start_button.clicked.connect(self.start_recording)
        button_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("结束监测")
        self.stop_button.setFont(QFont("Microsoft YaHei", 12))
        self.stop_button.clicked.connect(self.stop_recording)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)

        layout.addLayout(button_layout)

        # 添加波形显示部件
        self.waveform_widget = WaveformWidget()
        layout.addWidget(self.waveform_widget)

        # 创建状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("准备就绪")

    def read_config(self):
        """配置文件读取"""
        os.makedirs('./ClassVoiceMonitor/', exist_ok=True)
        config_file_dir = r'./ClassVoiceMonitor/config.json'

        def create_config():
            if not os.path.exists(config_file_dir):
                with open(config_file_dir, 'w+', encoding='utf-8') as config_file:
                    json.dump(config_default, config_file)
            logging.warning(f"已创建配置文件:{config_file_dir}")

        try:
            config_default = {
                'config_version': self.config_version,
                'window_width': self.width(),
                'window_height': self.height(),
                'max_rms': self.max_rms,
            }

            try:
                with open(config_file_dir, encoding='utf-8') as config_file:
                    # 读取并将配置赋值给变量
                    config = json.load(config_file)

                    self.max_rms = config['max_rms']
                    self.sensitivity_slider.setValue(self.max_rms)
                    self.sensitivity_value_label.setText(str(self.max_rms))

                    window_width = config['window_width']
                    window_height = config['window_height']
                    self.resize(window_width, window_height)

            except FileNotFoundError as e:
                logging.warning(f"文件不存在:{str(e)}.尝试创建配置文件...")
                create_config()


        except Exception as e:
            logging.error(f'配置文件读写错误:{str(e)}')
            QMessageBox.critical(self, '错误',
                                 '配置文件读写错误!\n请尝试删除配置文件夹中的config.json\n错误信息:' + str(e))

    def save_config(self):
        """配置文件写入"""
        try:
            config_file_dir = r'./ClassVoiceMonitor/config.json'
            with open(config_file_dir, 'r', encoding='utf-8') as config_file:
                # 读取格式并覆写值
                config = json.load(config_file)
                config['window_width'] = self.width()
                config['window_height'] = self.height()
                config['max_rms'] = self.max_rms
            with open(config_file_dir, 'w', encoding='utf-8') as config_file:
                # 写入覆写后的 config
                json.dump(config, config_file, ensure_ascii=False)
                logger.info(f"配置文件已保存:{config_file_dir}")
        except Exception as e:
            logging.critical(f"配置文件写入错误:{str(e)}")
            QMessageBox.critical(self, '错误', '配置文件写入错误！')

    # def resizeEvent(self, event):
    #     """重写窗口大小变化事件处理"""
    #     super().resizeEvent(event)
    #     # 取消未执行的保存操作，重新开始计时
    #     self.save_timer.stop()
    #     # 延迟500毫秒后触发保存
    #     self.save_timer.start(500)
    #     logging.debug(f"窗口尺寸已改变 → 宽度: {current_width}px, 高度: {current_height}px")

    def update_sensitivity(self, value):
        """更新灵敏度值"""
        self.max_rms = value
        self.sensitivity_value_label.setText(f"{value}")

    def start_recording(self):
        """开始录音"""
        try:
            self.init_audio()
            self.is_recording = True
            self.start_time = datetime.datetime.now()
            self.score = 0
            self.combo_count = 0
            self.rating_history = []
            self.combo_history = []

            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.status_bar.showMessage("开始监测")

        except Exception as e:
            error_msg = f"启动录音失败: {str(e)}"
            logger.error(error_msg)
            QMessageBox.critical(self, "错误", "无法启动录音设备，请检查麦克风设置")

    def stop_recording(self):
        """结束录音并生成报告"""
        self.is_recording = False

        # 停止定时器
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        if hasattr(self, 'rating_timer') and self.rating_timer.isActive():
            self.rating_timer.stop()

        # 关闭音频流
        if hasattr(self, 'stream'):
            self.stream.stop_stream()
            self.stream.close()
        if hasattr(self, 'audio'):
            self.audio.terminate()

        self.end_time = datetime.datetime.now()

        # 生成报告
        try:
            report_data = self.generate_report_data()
            report_data_text = self.generate_report_text(report_data)

            # 显示报告对话框
            report_dialog = ReportDialog(report_data_text, self)
            report_dialog.exec_()

        except Exception as e:
            logger.error(f"显示报告流程出错:{str(e)}")

        # 保存报告到文件
        self.save_report(report_data)

        # 重置界面
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_bar.showMessage("监测已结束")

    def generate_report_data(self):
        """生成报告数据"""
        duration = (self.end_time - self.start_time).total_seconds()
        avg_score_rate = self.score / duration if duration > 0 else 0

        # 统计评级次数
        critical_perfect_count = sum(1 for r in self.rating_history if r['rating'] == 'CRITICAL PERFECT!')
        perfect_count = sum(1 for r in self.rating_history if r['rating'] == 'Perfect!')
        great_count = sum(1 for r in self.rating_history if r['rating'] == 'Great!')
        good_count = sum(1 for r in self.rating_history if r['rating'] == 'Good')
        miss_count = sum(1 for r in self.rating_history if r['rating'] == 'Miss')

        total_ratings = len(self.rating_history)

        # 计算百分比
        critical_perfect_percent = (critical_perfect_count / total_ratings * 100) if total_ratings > 0 else 0
        perfect_percent = (perfect_count / total_ratings * 100) if total_ratings > 0 else 0
        great_percent = (great_count / total_ratings * 100) if total_ratings > 0 else 0
        good_percent = (good_count / total_ratings * 100) if total_ratings > 0 else 0
        miss_percent = (miss_count / total_ratings * 100) if total_ratings > 0 else 0

        # 计算连击统计
        total_combos = sum(self.combo_history)
        max_combo = max(self.combo_history) if self.combo_history else 0
        avg_combo_duration = sum(self.combo_history) / len(self.combo_history) if self.combo_history else 0

        return {
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'duration': int(duration),
            'total_score': self.score,
            'avg_score_rate': avg_score_rate,
            'total_combos': total_combos,
            'max_combo': max_combo,
            'avg_combo_duration': avg_combo_duration,
            'critical_perfect_count': critical_perfect_count,
            'perfect_count': perfect_count,
            'great_count': great_count,
            'good_count': good_count,
            'miss_count': miss_count,
            'critical_perfect_percent': critical_perfect_percent,
            'perfect_percent': perfect_percent,
            'great_percent': great_percent,
            'good_percent': good_percent,
            'miss_percent': miss_percent,
        }

    def save_report(self, report_data):
        """保存报告到文件"""
        # 创建日志目录
        log_dir = "./ClassVoiceMonitor/logs"
        os.makedirs(log_dir, exist_ok=True)

        # 生成文件名
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        filename = f"早读报告_{timestamp}.txt"
        filepath = os.path.join(log_dir, filename)

        # 写入文件
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(self.generate_report_text(report_data))
            logger.info(f"报告已保存到: {filepath}")
        except Exception as e:
            logger.error(f"保存报告失败: {str(e)}")

    def generate_report_text(self, report_data):
        """生成报告文本"""
        return f"""
早读报告
{'=' * 25}
基本信息:
    开始时间: {report_data['start_time']}
    结束时间: {report_data['end_time']}
    记录时长: {report_data['duration']} 秒
    总得分: {report_data['total_score']}
    平均得分率: {report_data['avg_score_rate']:.2f} 分/秒
            
连击统计:
    总连击次数: {report_data['total_combos']}
    最高连击: {report_data['max_combo']} 
    平均连击时长: {report_data['avg_combo_duration']:.1f} 秒
            
评级分布:
    Critical Perfect (>95): {report_data['critical_perfect_count']} ({report_data['critical_perfect_percent']:.1f}%)
    Perfect (>85):          {report_data['perfect_count']} ({report_data['perfect_percent']:.1f}%)
    Great (>70):            {report_data['great_count']} ({report_data['great_percent']:.1f}%)
    Good (>50):             {report_data['good_count']} ({report_data['good_percent']:.1f}%)
    Miss (<50):             {report_data['miss_count']} ({report_data['miss_percent']:.1f}%)
{'=' * 25}
        """

    def init_audio(self):
        """初始化音频设备"""
        try:
            logger.info("正在初始化音频设备...")
            self.audio = pyaudio.PyAudio()

            # 音频流参数
            self.FORMAT = pyaudio.paInt16
            self.CHANNELS = 1
            self.RATE = 44100
            self.CHUNK = 1024

            # 打开音频流
            self.stream = self.audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK,
                input_device_index=None  # 使用默认设备
            )

            logger.info(f"音频设备初始化成功 - 采样率: {self.RATE}, 块大小: {self.CHUNK}")

            # 创建定时器用于实时更新
            self.timer = QTimer()
            self.timer.timeout.connect(self.update_volume)
            self.timer.start(50)  # 每50ms更新一次，更快的响应

            # 创建定时器用于每秒评分
            self.rating_timer = QTimer()
            self.rating_timer.timeout.connect(self.update_rating)
            self.rating_timer.start(1000)  # 每秒更新一次评分

            self.status_bar.showMessage("正在监听麦克风...")

        except Exception as e:
            error_msg = f"音频设备初始化失败: {str(e)}"
            logger.info(error_msg)
            traceback.print_exc()
            self.status_bar.showMessage("初始化失败 - 请查看控制台")

    def calculate_volume_level(self, data):
        """计算音频数据的音量级别（0-1之间的值）"""
        try:
            # 将字节数据转换为numpy数组
            audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)

            # 计算RMS（均方根）值
            rms = np.sqrt(np.mean(audio_data ** 2))

            # 将RMS值转换为0-1之间的相对级别
            # 使用当前max_rms值作为最大阈值
            max_rms = self.max_rms

            # 计算相对级别，使用对数响应更符合人耳感知
            if rms < 1:
                level = 0.0
            else:
                # 使用对数缩放，使低音量更敏感
                level = min(1.0, np.log10(rms) / np.log10(max_rms))

            return level, rms

        except Exception as e:
            logger.error(f"音量计算错误: {str(e)}")
            return 0.0, 0.0

    def smooth_level(self, level):
        """平滑音频级别，减少跳动"""
        self.audio_level_history.append(level)
        if len(self.audio_level_history) > self.history_size:
            self.audio_level_history.pop(0)

        # 使用加权平均，最近的样本权重更高
        weights = np.linspace(0.5, 1.0, len(self.audio_level_history))
        weighted_sum = sum(l * w for l, w in zip(self.audio_level_history, weights))
        total_weight = sum(weights)

        return weighted_sum / total_weight

    def update_volume(self):
        """更新音量显示"""
        try:
            # 检查音频流是否有效
            if not self.stream or not self.stream.is_active():
                logger.error("音频流无效或未激活")
                return

            # 读取音频数据
            data = self.stream.read(self.CHUNK, exception_on_overflow=False)

            # 计算音量级别
            level, rms = self.calculate_volume_level(data)

            # 平滑级别值
            smoothed_level = self.smooth_level(level)

            # 转换为百分比显示
            percentage = int(smoothed_level * 100)
            self.last_level = percentage  # 保存当前级别用于评分

            # 更新标签显示
            self.level_label.setText(f"音量级别: {percentage}%")

            # 更新进度条
            self.progress_bar.setValue(percentage)

            # 更新波形显示
            self.waveform_widget.add_data_point(percentage)

            # 更新状态栏调试信息
            self.status_bar.showMessage(f"RMS: {rms:.1f} | 级别: {smoothed_level:.3f} | 灵敏度: {self.max_rms}")

        except Exception as e:
            error_msg = f"更新音量显示时出错: {str(e)}"
            logger.error(error_msg)
            traceback.print_exc()
            self.status_bar.showMessage("读取错误 - 请查看控制台")

    def update_rating(self):
        if not self.is_recording:
            return

        level = self.last_level

        rating = ""
        points = 0

        if level > 95:
            rating = "CRITICAL PERFECT!"
            points = 100
        elif level > 85:
            rating = "Perfect!"
            points = 80
        elif level >= 70:
            rating = "Great!"
            points = 50
        elif level >= 50:
            rating = "Good"
            points = 20
        else:
            rating = "Miss"
            points = -20

            # 更新连击计数
        if rating in ["Great!", "Perfect!", "CRITICAL PERFECT!"]:
            self.combo_count += 1
        else:
            # 连击中断，记录连击历史
            if self.combo_count >= 5:
                self.combo_history.append(self.combo_count)
            self.combo_count = 0

        # 计算连击奖励
        combo_bonus = 0
        if self.combo_count >= 5:
            combo_bonus = int(points * (self.combo_count - 4) * 0.1)
            display_text = f" Combo x{self.combo_count} +{combo_bonus + points}"
        else:
            points_display = f"+{points}" if points > 0 else str(points)
            display_text = f" {points_display}"

        self.score += points

        self.rating_history.append({
            'time': datetime.datetime.now(),
            'level': level,
            'rating': rating,
            'points': points,
            'combo_bonus': combo_bonus,
            'combo_count': self.combo_count
        })

        # 更新显示
        self.score_label.setText(f"得分: {self.score}")
        self.rating_label.setText(rating)
        self.combo_label.setText(display_text)

        # 设置Rating的颜色
        if rating.startswith("CRITICAL"):
            self.rating_label.setStyleSheet("color: #ffe800; font-weight: bold;")
        elif rating.startswith("Perfect"):
            self.rating_label.setStyleSheet("color: #ffa300; font-weight: bold;")
        elif rating.startswith("Great"):
            self.rating_label.setStyleSheet("color: #ea1ac1; font-weight: bold;")
        elif rating.startswith("Good"):
            self.rating_label.setStyleSheet("color: #5eff00;")
        else:
            self.rating_label.setStyleSheet("color: #95a5a6;")

        if rating.startswith('CRITICAL'):
            logger.info(f"{rating.upper()}, 得分变化: {points} + {combo_bonus} , 总得分: {self.score}")

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        logger.info("正在关闭应用，清理资源...")
        self.save_config()
        try:
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()
                logger.info("定时器已停止")

            if hasattr(self, 'rating_timer') and self.rating_timer.isActive():
                self.rating_timer.stop()
                logger.info("评分定时器已停止")

            if hasattr(self, 'stream'):
                self.stream.stop_stream()
                self.stream.close()
                logger.info("音频流已关闭")

            if hasattr(self, 'audio'):
                self.audio.terminate()
                logger.info("PyAudio已终止")

        except Exception as e:
            logger.error(f"清理资源时出错: {str(e)}")

        event.accept()


def main():
    app = QApplication(sys.argv)

    app.setStyle('Fusion')

    window = Main()
    window.show()

    logger.info("应用启动成功")

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
