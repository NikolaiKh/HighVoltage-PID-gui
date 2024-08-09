import sys
import time
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui
import numpy as np
# import instruments as ik
# import instruments.units as u
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QApplication, QWidget, QGraphicsScene, QFileDialog
from PyQt6.QtCore import QObject, QThreadPool, QRunnable, pyqtSlot, pyqtSignal
import traceback
import ctypes
import Lockin_SR_class as Lockin_class
from pymeasure.instruments.agilent import Agilent34450A
from simple_pid import PID


class WorkerSignals(QObject):
    '''
    Defines the signals available from a running worker thread.

    Supported signals are:

    finished
        No data

    error
        tuple (exctype, value, traceback.format_exc() )

    result
        object data returned from processing, anything

    '''
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)


class Worker(QRunnable):
    # used for LIA and XPS initialization
    '''
    Worker thread for any function
    Inherits from QRunnable to handler worker thread setup, signals and wrap-up.
    :param callback: The function callback to run on this worker thread. Supplied args and
                     kwargs will be passed through to the runner.
    :type callback: function
    :param args: Arguments to pass to the callback function
    :param kwargs: Keywords to pass to the callback function

    taken from https://www.pythonguis.com/tutorials/multithreading-pyqt-applications-qthreadpool/
    '''

    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()
        # Store constructor arguments (re-used for processing)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        '''
        Initialise the runner function with passed args, kwargs.
        '''

        # Retrieve args/kwargs here; and fire processing using them
        try:
            result = self.fn(
                *self.args, **self.kwargs
            )
        except:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            self.signals.result.emit(result)  # Return the result of the processing
        finally:
            self.signals.finished.emit()  # Done


# class PID:
#     def __init__(self, Kp, Ki, Kd):
#         self.Kp = Kp
#         self.Ki = Ki
#         self.Kd = Kd
#         self.last_error = 0
#         self.integral = 0
#
#     def update(self, error, dt):
#         # upper and lower bounds on heater level
#         max_aux = 1
#         min_aux = 0
#         derivative = (error - self.last_error) / dt
#         self.integral += error * dt
#         output = self.Kp * error + self.Ki * self.integral + self.Kd * derivative
#         # implement anti-reset windup
#         # if output < min_aux or output > max_aux:
#         #     self.integral = self.integral - self.Ki * error * dt
#         #     output = max(min_aux, min(max_aux, output))
#         self.last_error = error
#         return output


class TemperatureControl(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.old_setpoint = 0
        self.setpoint = 0
        self.setWindowTitle("HV Control")
        self.setWindowIcon(QtGui.QIcon('efield_icon.png'))

        self.layout = QtWidgets.QVBoxLayout(self)

        self.lia_address_label = QtWidgets.QLabel("Lock-in GPIB address")
        self.lia_address_input = QtWidgets.QLineEdit('8')

        self.mm_address_label = QtWidgets.QLabel("Multimeter address")
        self.mm_address_input = QtWidgets.QLineEdit('USB0::0x0957::0x0618::MY52050080::INSTR')

        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.clicked.connect(self.lia_mm_init)

        self.status_label = QtWidgets.QLabel("Status: NOT connected")

        self.current_voltage_label = QtWidgets.QLabel("Current voltage (V)")
        self.current_voltage_input = QtWidgets.QLineEdit('---')

        self.set_point_label = QtWidgets.QLabel("Set point voltage (V)")
        self.set_point_curr_label = QtWidgets.QLabel("Current set point: ---- V")
        self.set_point_input = QtWidgets.QLineEdit('---')

        self.set_temp_button = QtWidgets.QPushButton("Set voltage (V)")
        self.set_temp_button.clicked.connect(self.set_point_schange)

        self.layout.addWidget(self.lia_address_label)
        self.layout.addWidget(self.lia_address_input)
        self.layout.addWidget(self.connect_button)
        self.layout.addWidget(self.status_label)
        self.layout.addWidget(self.mm_address_label)
        self.layout.addWidget(self.mm_address_input)
        self.layout.addWidget(self.current_voltage_label)
        self.layout.addWidget(self.current_voltage_input)
        self.layout.addWidget(self.set_point_curr_label)
        self.layout.addWidget(self.set_point_label)
        self.layout.addWidget(self.set_point_input)
        self.layout.addWidget(self.set_temp_button)

        # plot widget
        self.canvas = pg.GraphicsLayoutWidget()
        # self.canvas.setBackground((255, 255, 255))
        self.layout.addWidget(self.canvas)
        #  line plot
        # pen = pg.mkPen(color=(0, 0, 255), width=5)
        self.temperature_plot = self.canvas.addPlot()
        self.temperature_plot.addLegend()
        pen = pg.mkPen(color=(215, 48, 39), width=1)
        self.plot_set_temp = self.temperature_plot.plot(pen=pen, name="SetPoint")
        pen = pg.mkPen(color=(69, 117, 180), width=1)
        self.plot_curr_temp = self.temperature_plot.plot(pen=pen, name="Current voltage")
        self.temperature_plot.setTitle("Voltage vs Time")
        self.temperature_plot.setLabel("left", "Voltage (K)")
        self.temperature_plot.setLabel("bottom", "Time (arb.units)")

        self.x = np.linspace(0, 50., num=100)
        self.X, self.Y = np.meshgrid(self.x, self.x)
        self.counter = 0
        self.xdata = []
        self.ydata = []
        self.setpoint_data = []

        # setup thread pool
        self.threadpool = QThreadPool()
        print("Multithreading with maximum %d threads" % self.threadpool.maxThreadCount())


    def lia_mm_init(self):
        # connect LIA
        self.lockin_id = self.lia_address_input.text()  # set lock-in addres
        self.lia = Lockin_class.Lockin(self.lockin_id)
        self.status_label.setText(self.lia.state)
        # connect multimeter line
        mm_address = self.mm_address_input.text()
        self.multimeter = Agilent34450A(mm_address)
        self.current_voltage_input.setText(str(self.multimeter.voltage))
        self.set_point_input.setText(f'{self.setpoint}')
        self.setpoint = float(self.set_point_input.text())
        # set PID control
        self.pid = PID(Kp=0.001, Ki=0.001, Kd=0.00002, setpoint=0)
        self.pid.output_limits = (0, 3.01)
        self.pid.sample_time = 0.01  # Update every 0.01 seconds
        ### start updating the window and data ###
        self._update()

    def set_point_schange(self):
        self.old_setpoint = float(self.setpoint)
        self.setpoint = float(self.set_point_input.text())

    def set_point_press(self):
        # Pass the function to execute
        worker = Worker(self.set_point_schange)  # Any other args, kwargs are passed to the run function
        # Execute
        self.threadpool.start(worker)

    def _update(self):
        curr_voltage = float(self.multimeter.voltage)
        self.current_voltage_input.setText(str(curr_voltage))
        self.set_point_curr_label.setText(f'Current set point: {self.setpoint}V')

        # use PID
        dt = 0.1
        aux = 3
        self.pid.setpoint = self.setpoint
        control_signal = self.pid(curr_voltage)
        self.lia.set_aux(aux, control_signal)
        # error = self.setpoint - curr_voltage
        # control_signal = self.pid.update(error, dt)
        # self.lia.set_aux(aux, current_aux_voltage + control_signal * dt)
        # plot current and set voltage
        self.xdata.append(self.counter)
        self.ydata.append(curr_voltage)
        self.setpoint_data.append(self.setpoint)
        if self.counter > 100:
            del self.xdata[0]
            del self.ydata[0]
            del self.setpoint_data[0]
        self.plot_set_temp.setData(self.xdata, self.setpoint_data)
        self.plot_curr_temp.setData(self.xdata, self.ydata)
        QtCore.QTimer.singleShot(100, self._update)
        self.counter += 1



if __name__ == "__main__":
    myappid = 'Nikolai.HV.control.10'  # arbitrary string
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    app = QtWidgets.QApplication([])
    app.setStyle('Fusion')
    window = TemperatureControl()
    window.show()
    app.exec()
