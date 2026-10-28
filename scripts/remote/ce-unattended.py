#!/usr/bin/env python3
# =============================================================================
# ce-unattended.py — run the Nutanix CE installer (Phoenix) without its text UI
# =============================================================================
# WHERE IT RUNS : inside the CE installer, as root, after ce-bm-prep.sh
#                 cd /root/phoenix && python3 /root/ce-unattended.py
# HOW           : Nutanix's own ./phoenix is executed unchanged. In CE mode it
#                 calls gui.get_params(gui.CEGui), which detects the hardware,
#                 shows the curses screen, then turns the screen's fields into
#                 the install parameters and validates them. Only the curses
#                 screen is replaced: instead of drawing it, the same fields the
#                 screen would set (gui.CEGui.init_page / get_extra_params) are
#                 filled from the environment. Hardware detection, validation,
#                 imaging and first-boot setup are all stock.
#                 REBOOT=false is Phoenix's own switch (imagingUtil.image_node,
#                 read through sysUtil.parse_cmd_line from argv), and "N" is
#                 fed to the final reboot question: AHV must not boot before
#                 ahv-bm-finalize.sh.
# ENVIRONMENT   : CE_HOST_IP, CE_CVM_IP, CE_NETMASK, CE_GATEWAY   (required)
#                 CE_ACCEPT_EULA=yes                              (required: you
#                   accept the Nutanix CE EULA, /root/phoenix/ce_eula.txt)
#                 CE_HYP_DISK   (default sda, the boot volume)
#                 CE_CVM_DISK   (default: smallest other disk >= 200 GB)
#                 CE_DATA_DISKS (default: all remaining disks >= 200 GB, comma-separated)
# =============================================================================
import os
import runpy
import sys

PHOENIX = "/root/phoenix"


def env(name, default=None):
  v = os.environ.get(name, default)
  if v in (None, ""):
    sys.exit("ce-unattended: %s is not set" % name)
  return v


host_ip, cvm_ip = env("CE_HOST_IP"), env("CE_CVM_IP")
netmask, gateway = env("CE_NETMASK"), env("CE_GATEWAY")
if env("CE_ACCEPT_EULA", "no").lower() != "yes":
  sys.exit("ce-unattended: set CE_ACCEPT_EULA=yes to accept the Nutanix CE EULA "
           "(%s/ce_eula.txt)" % PHOENIX)

os.environ["COMMUNITY_EDITION"] = "1"
os.environ.setdefault("TERM", "dumb")
os.chdir(PHOENIX)
sys.path.insert(0, PHOENIX)

import builtins                              # noqa: E402
import gui                                   # noqa: E402  (Phoenix modules)
import imagingUtil                           # noqa: E402
from gui_widgets import FakeCheckBox         # noqa: E402
from hardware_inventory import disk_info     # noqa: E402


def pick_disks(disks):
  """H/C/D like the screen would, but sized for OCI: boot volume = H,
  the smaller block volume = C (CVM boot), the rest = D."""
  hyp = os.environ.get("CE_HYP_DISK") or "sda"
  iso = disk_info.choose_ce_disk_defaults(disks).get("PHOENIX_ISO", []) or []
  rest = sorted((d for d in disks
                 if d != hyp and d not in iso and disks[d].size >= 200.0),
                key=lambda d: disks[d].size)
  cvm = os.environ.get("CE_CVM_DISK") or (rest[0] if rest else None)
  data = [d for d in os.environ.get("CE_DATA_DISKS", "").split(",") if d] or \
         [d for d in rest if d != cvm]
  for d in [hyp, cvm] + data:
    if d not in disks:
      sys.exit("ce-unattended: disk %s not found; disks: %s" % (d, sorted(disks)))
  if not disks[cvm].isSSD:
    sys.exit("ce-unattended: CVM disk %s is not flagged SSD (ce-bm-prep.sh step 3)" % cvm)
  if not data:
    sys.exit("ce-unattended: no data disk found")
  return hyp, cvm, data


def unattended_screen(interactive_ui):
  """Stands in for curses.wrapper(gui.interactive_ui): sets the fields the
  CE screen sets, then reports "Start pressed" (True) to gui.run_gui."""
  g = interactive_ui.__self__
  ahv = gui.get_packaged_hyp()[0]
  ahv_label = "AHV ({})".format(os.path.basename(ahv.path)[:-4])

  class Selected(object):
    def get_selected(self):
      return ahv_label

  g.disks = disk_info.collect_disk_info(skip_part_info=False)
  hyp, cvm, data = pick_disks(g.disks)
  g.hyp_select = Selected()
  g.hyp_boot_disk, g.cvm_boot_disk, g.cvm_data_disks = [hyp], [cvm], data
  g.host_ip = gui.FakeText(host_ip)
  g.svm_ip = gui.FakeText(cvm_ip)
  g.host_subnet_mask = g.svm_subnet_mask = gui.FakeText(netmask)
  g.default_gw = g.svm_default_gw = gui.FakeText(gateway)
  g.acceptbox = FakeCheckBox(True)
  g.eula = type("E", (), {"scrolled_to_end": True})()
  # the rest exactly as CEGui.init_page sets it
  g.action = set([gui.INSTALL_HYPERVISOR, gui.INSTALL_SVM])
  g.nos = gui.get_packaged_nos()[0]
  g.position = gui.FakeText("A")
  g.block_id = gui.FakeText(str(gui.uuid4()).split("-")[0])
  g.node_serial = gui.FakeText(gui.gp.p_list.node_serial)
  g.cluster_id = gui.FakeText(gui.gp.p_list.cluster_id)
  g.svm_gb_ram = gui.FakeText(gui.gp.p_list.svm_gb_ram)
  g.page = g.finalPage

  print("ce-unattended: AOS %s, %s" % (g.nos.version, ahv_label))
  print("ce-unattended: disks  H=%s (%.0f GB)  C=%s (%.0f GB)  D=%s" % (
      hyp, g.disks[hyp].size, cvm, g.disks[cvm].size,
      ", ".join("%s (%.0f GB)" % (d, g.disks[d].size) for d in data)))
  print("ce-unattended: host %s  CVM %s  mask %s  gateway %s  EULA accepted" % (
      host_ip, cvm_ip, netmask, gateway))
  sys.stdout.flush()
  return True


def answer(prompt=""):
  """The only question expected is "reboot now?" -> N (REBOOT=false already
  prevents the reboot). Anything else, e.g. "Press 'enter' to continue" after
  a validation error, would wait forever: stop with the question instead."""
  sys.stdout.write(prompt)
  if "reboot now" in prompt:
    print("N   (ce-unattended)")
    return "N"
  print()
  raise SystemExit("ce-unattended: unexpected question from the installer: %r" % prompt)


gui.curses.wrapper = unattended_screen
builtins.input = answer
imagingUtil.input = answer      # bound at import: "from six.moves import input"
sys.stdin = open(os.devnull)
sys.argv = [os.path.join(PHOENIX, "phoenix"), "REBOOT=false"]
runpy.run_path(sys.argv[0], run_name="__main__")
