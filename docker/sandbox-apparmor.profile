# Derived from moby v28.0.0 profiles/apparmor/template.go.
# Namespace-local mounts are required by Bazel's hermetic sandbox. Outer
# capabilities remain empty; seccomp and the readonly container still apply.
abi <abi/4.0>,
#include <tunables/global>
profile @YOMUX_PROFILE@ flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  userns,
  network,
  capability,
  file,
  mount,
  pivot_root,
  umount,
  signal (receive) peer=unconfined,
  signal (receive) peer=runc,
  signal (receive) peer=crun,
  signal (send,receive) peer=@YOMUX_PROFILE@,
  deny /proc/* w,
  deny /proc/{[^1-9],[^1-9][^0-9],[^1-9s][^0-9y][^0-9s],[^1-9][^0-9][^0-9][^0-9/]*}/** w,
  deny /proc/sys/[^k]** w,
  deny /proc/sys/kernel/{?,??,[^s][^h][^m]**} w,
  deny /proc/sysrq-trigger rwklx,
  deny /proc/kcore rwklx,
  deny /sys/[^f]*/** wklx,
  deny /sys/f[^s]*/** wklx,
  deny /sys/fs/[^c]*/** wklx,
  deny /sys/fs/c[^g]*/** wklx,
  deny /sys/fs/cg[^r]*/** wklx,
  deny /sys/firmware/** rwklx,
  deny /sys/devices/virtual/powercap/** rwklx,
  deny /sys/kernel/security/** rwklx,
  ptrace (trace,read,tracedby,readby) peer=@YOMUX_PROFILE@,
}
