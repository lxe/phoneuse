using System.Diagnostics;
using System.Runtime.InteropServices;

namespace PhoneUseMirror;

/// <summary>
/// A Windows job object holding a process and everything it starts, so all of them can be stopped at
/// once. Process.Kill(entireProcessTree) isn't enough for Python: a venv's python.exe is a launcher that
/// starts the real interpreter as its child, and once the launcher is killed its child is no longer found
/// under it, so it keeps running (pip went on installing after Cancel). Processes started inside the job
/// stay in it whoever their parent is.
/// </summary>
public sealed class ProcessJob : IDisposable
{
    IntPtr _job = CreateJobObject(IntPtr.Zero, null);

    /// <summary>Puts the process in the job; what it starts from now on joins it too.</summary>
    public bool Add(Process p) => _job != IntPtr.Zero && AssignProcessToJobObject(_job, p.Handle);

    /// <summary>Ends every process in the job.</summary>
    public void Kill()
    {
        if (_job != IntPtr.Zero) TerminateJobObject(_job, 1);
    }

    public void Dispose()
    {
        if (_job == IntPtr.Zero) return;
        CloseHandle(_job);  // without KILL_ON_JOB_CLOSE: closing it leaves the processes running
        _job = IntPtr.Zero;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attributes, string? name);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
}
