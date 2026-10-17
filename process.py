"""Linux/macOS process management built directly on fork(), exec(), waitpid() and signals.

Every job runs as a child process created with os.fork() + os.execvp("/bin/sh", ...).
The child becomes the leader of its own *process group* (setpgid), so a signal sent
with os.killpg() reaches the shell AND everything it spawned.  Standard output and
standard error of the child are connected to one pipe that the scheduler reads
without blocking.
"""

import os
import signal

# Life-cycle states of a live process, as seen by the scheduler.
RUNNING = "running"        # executing on a worker slot
SUSPENDED = "suspended"    # stopped with SIGSTOP and waiting in the ready queue (Round Robin preemption)
PAUSED = "paused"          # stopped with SIGSTOP because the user paused the job

MAX_OUTPUT_BYTES = 64 * 1024


class ManagedProcess(object):
    """A running child process plus the bookkeeping the scheduler needs."""

    def __init__(self, pid, read_fd):
        self.pid = pid
        self.pgid = pid                 # the child is its own process-group leader
        self.fd = read_fd               # read end of the stdout/stderr pipe
        self.status = RUNNING
        # filled in by the engine
        self.job_id = None
        self.job_name = None
        self.run_id = None
        self.timeout = 0
        self.start_time = None          # wall-clock start
        self.slice_started = None       # when the current execution slice began (None while stopped)
        self.active_time = 0.0          # accumulated time spent actually executing
        self.queued_at = None           # when it re-entered the ready queue
        self.preemptions = 0
        self.reason = None              # timeout | killed | deleted | interrupted
        self.term_at = None             # when SIGTERM was sent
        self.sigkill_sent = False
        self.wait_status = None
        self._buffer = bytearray()
        self.truncated = False

    # -- creation ------------------------------------------------------------
    @classmethod
    def spawn(cls, command):
        """fork() a child, put it in its own process group and exec `sh -c command`."""
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:                                    # ----- child -----
            try:
                os.setpgid(0, 0)                        # new process group = child pid
                os.close(read_fd)
                os.dup2(write_fd, 1)                    # stdout -> pipe
                os.dup2(write_fd, 2)                    # stderr -> pipe
                if write_fd > 2:
                    os.close(write_fd)
                devnull = os.open(os.devnull, os.O_RDONLY)
                os.dup2(devnull, 0)                     # stdin <- /dev/null
                os.execvp("/bin/sh", ["sh", "-c", command])
            except BaseException:
                pass
            os._exit(127)                               # exec failed
        # ----- parent -----
        os.close(write_fd)
        try:
            os.setpgid(pid, pid)                        # avoid a race with the child's own setpgid
        except OSError:
            pass
        os.set_blocking(read_fd, False)
        return cls(pid, read_fd)

    # -- signals -------------------------------------------------------------
    def send(self, sig):
        """Send a signal to the whole process group.  Returns False if it no longer exists."""
        try:
            os.killpg(self.pgid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    # -- time accounting -----------------------------------------------------
    def active_total(self, now):
        extra = (now - self.slice_started) if self.slice_started is not None else 0.0
        return self.active_time + extra

    def slice_elapsed(self, now):
        return (now - self.slice_started) if self.slice_started is not None else 0.0

    # -- output --------------------------------------------------------------
    def read_output(self):
        """Drain whatever the child has written so far (never blocks)."""
        while self.fd is not None:
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                self._close_fd()
                return
            if not chunk:                               # EOF: every writer closed the pipe
                self._close_fd()
                return
            room = MAX_OUTPUT_BYTES - len(self._buffer)
            if room > 0:
                self._buffer.extend(chunk[:room])
            if len(chunk) > room:
                self.truncated = True

    def _close_fd(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def close(self):
        self._close_fd()

    def output_text(self):
        text = self._buffer.decode("utf-8", errors="replace")
        if self.truncated:
            text += "\n[... output truncated at %d bytes ...]" % MAX_OUTPUT_BYTES
        return text

    # -- reaping -------------------------------------------------------------
    def poll(self):
        """waitpid(WNOHANG): True once the child has terminated (and has been reaped)."""
        if self.wait_status is not None:
            return True
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.wait_status = 0
            return True
        if pid == 0:
            return False
        self.wait_status = status
        return True

    @property
    def exit_code(self):
        """Exit status, or -N if the child was killed by signal N (None while alive)."""
        if self.wait_status is None:
            return None
        if os.WIFSIGNALED(self.wait_status):
            return -os.WTERMSIG(self.wait_status)
        if os.WIFEXITED(self.wait_status):
            return os.WEXITSTATUS(self.wait_status)
        return -1


def describe_exit(code):
    """Human-readable description of an exit code."""
    if code is None:
        return "unknown"
    if code < 0:
        try:
            return "killed by signal %s" % signal.Signals(-code).name
        except ValueError:
            return "killed by signal %d" % -code
    return "exit code %d" % code
