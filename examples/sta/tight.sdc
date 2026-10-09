# Minimal SDC for the M2.5 STA demo (design: simple_counter).
#
# SDC tells static timing analysis:
#   - which signal is the clock and how fast it toggles (period)
#   - how much delay external logic adds before inputs / after outputs
#
# Port names were checked against examples/sta/design.v (clk, rst_n, count).
# The 0.5 ns period is intentionally aggressive for a 45 nm demo library so
# the timing report can show real slack numbers - possibly negative ones.
# A negative WNS with exit code 0 is still an EXECUTION success (M2.5):
# timing verdicts are the QA engine's job (M5), not the driver's.
create_clock -name clk -period 0.2 [get_ports clk]
set_input_delay 0.05 -clock clk [get_ports rst_n]
set_output_delay 0.05 -clock clk [all_outputs]
