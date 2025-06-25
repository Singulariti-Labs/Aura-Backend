
class TaskCancelledException(Exception):
    """
    Class to raise custom exception when a task is cancelled or aborted.
    """
    
    def __init__(self, task_id: str, message: str = "Task was cancelled"):
        self.task_id = task_id
        super().__init__(message)