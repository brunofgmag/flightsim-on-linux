from PyQt6.QtCore import QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QLayout, QSizePolicy


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=12):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def arrange(self, rect, measure_only):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self.items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + spacing
                line_height = 0
            if not measure_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class Task(QObject):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class TaskRunner(QRunnable):
    def __init__(self, function, task):
        super().__init__()
        self.function = function
        self.task = task

    def run(self):
        try:
            result = self.function(self.task.progress.emit)
        except Exception as error:
            self.task.failed.emit(str(error) or type(error).__name__)
        else:
            self.task.done.emit(result)


class Tasks:
    def __init__(self):
        self.running = set()

    def start(self, function, on_done=None, on_failed=None, on_progress=None):
        task = Task()
        self.running.add(task)
        if on_progress:
            task.progress.connect(on_progress)
        if on_done:
            task.done.connect(on_done)
        if on_failed:
            task.failed.connect(on_failed)
        task.done.connect(lambda _: self.running.discard(task))
        task.failed.connect(lambda _: self.running.discard(task))
        QThreadPool.globalInstance().start(TaskRunner(function, task))
        return task


def expanding(widget):
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return widget
