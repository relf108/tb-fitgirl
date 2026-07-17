/* Become a process-group leader, then exec the real program.
 *
 * Flutter cancels the bridge with kill -TERM -- -<pid>, which only works
 * when the PID it tracks is the process-group leader. PyInstaller's Linux
 * bootloader is a parent of the Python process; setpgid in Python therefore
 * only moves the child. This tiny wrapper owns the group before exec so the
 * bootloader (same PID after exec) and all descendants share one group.
 *
 * Deliberately not setsid(): a new session detaches from the terminal and
 * hangs Proton's unpacker (kernel snd_power_wait on installer audio).
 */
#include <unistd.h>

int main(int argc, char **argv) {
  if (argc < 2) {
    return 127;
  }
  (void)setpgid(0, 0);
  execv(argv[1], argv + 1);
  return 127;
}
