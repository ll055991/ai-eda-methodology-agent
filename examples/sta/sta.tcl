# Minimal static timing analysis script for the M2.5 demo (OpenSTA 3.1.0).
#
# STA needs three things (this is the whole point of the demo):
#   1. Liberty  - timing models of the standard cells used in the netlist
#   2. Netlist  - WHICH cells are instantiated and how they are connected
#   3. SDC      - clock definition and input/output constraints
#
# OpenSTA writes its reports to STDOUT (like the open-source flows do);
# OpenSTA 3.1.0 has no `redirect` command. The platform therefore archives
# the whole tool stdout as the timing report artifact
# (artifacts/timing.rpt) - see RunService.run_sta.
read_liberty demo_nangate45_typ.lib
read_verilog mapped.v
link_design simple_counter
read_sdc timing.sdc
report_checks
report_worst_slack
report_tns
