"""Observe Windows global media playback without opening media files or reading titles."""
import base64
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading


# Windows PowerShell projects the system's existing Windows Runtime API. Keep
# one hidden process while sensing is enabled instead of starting one per poll.
_QUERY=r"""
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$managerType=[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetGenericArguments().Count -eq 1 -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name.StartsWith('IAsyncOperation')
} | Select-Object -First 1
$operation=$managerType::RequestAsync()
$task=$asTask.MakeGenericMethod($managerType).Invoke($null,@($operation))
$manager=$task.GetAwaiter().GetResult()
while ($null -ne [Console]::ReadLine()) {
  try {
    $items=foreach ($session in $manager.GetSessions()) {
      try {
        $info=$session.GetPlaybackInfo()
        $state=([string]$info.PlaybackStatus).ToLowerInvariant()
        if ($state -notin @('playing','paused','stopped')) {continue}
        $kind=switch ([string]$info.PlaybackType) {
          'Music' {'audio'} 'Video' {'video'} 'Image' {'image'} default {'unknown'}
        }
        $id=[string]$session.SourceAppUserModelId
        if ($id -in @('AtriLocal.DesktopCompanion','Atri.DesktopCompanion')) {$kind='own'}
        "$state|$kind"
      } catch {continue}
    }
    [Console]::WriteLine(($items -join ','))
  } catch {[Console]::WriteLine('unavailable')}
}
"""


def _summary(line):
    if line=="unavailable":
        return "unavailable","unknown"
    rows=[item.split("|") for item in line.split(",") if item]
    if any(len(row)!=2 or row[0] not in ("playing","paused","stopped")
           or row[1] not in ("audio","video","image","unknown","own") for row in rows):
        return "unavailable","unknown"
    external=[row for row in rows if row[1]!="own"]
    for state in ("playing","paused","stopped"):
        kinds={kind for status,kind in external or rows if status==state}
        if kinds:
            return state,kinds.pop() if len(kinds)==1 else "unknown"
    return "none","unknown"


class MediaObserver:
    """Sample off the GUI thread every four seconds and close when disabling.

    'none' means no exposed playing, paused or stopped session. The observer
    cannot establish what applications outside Windows media controls are doing.
    """
    def __init__(self):
        self._lock=threading.Lock()
        self._reading=threading.Lock()
        self._worker=None
        self._last=None

    def _start(self):
        shell=Path(os.environ.get("SystemRoot") or r"C:\Windows")/"System32/WindowsPowerShell/v1.0/powershell.exe"
        encoded=base64.b64encode(_QUERY.encode("utf-16-le")).decode("ascii")
        proc=subprocess.Popen([str(shell),"-NoLogo","-NoProfile","-NonInteractive","-EncodedCommand",encoded],
                            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)
        replies=Queue()
        def receive():
            try:
                with proc.stdout:
                    for raw in proc.stdout:
                        replies.put(raw.decode("ascii").strip())
            except (OSError,ValueError):
                pass
            finally:
                replies.put(None)
        threading.Thread(target=receive,daemon=True,name="atri-media-state").start()
        self._worker=(proc,replies)
        return self._worker

    def sample(self):
        state,kind="unavailable","unknown"
        with self._reading:
            if os.name=="nt":
                worker=None
                try:
                    with self._lock:
                        worker=self._worker or self._start()
                        proc,replies=worker
                        proc.stdin.write(b"?\n");proc.stdin.flush()
                    line=replies.get(timeout=3)
                    if line is None:raise OSError("系统媒体状态不可用")
                    state,kind=_summary(line)
                except (OSError,ValueError,Empty):
                    with self._lock:
                        if worker is not None and self._worker is worker:self._stop()
                with self._lock:
                    if worker is not None and self._worker is not worker:
                        return {"state":"unavailable","kind":"unknown","changed":False}
            with self._lock:
                previous=self._last
                self._last=(state,kind)
                return {"state":state,"kind":kind,"changed":previous!=self._last and
                        (previous is not None or state in ("playing","paused","stopped"))}

    def _stop(self):
        worker,self._worker=self._worker,None
        if worker:
            proc,_=worker
            try:proc.stdin.close()
            except OSError:pass
            if proc.poll() is None:
                try:proc.terminate()
                except OSError:pass

    def close(self):
        with self._lock:
            self._stop()
            self._last=None
