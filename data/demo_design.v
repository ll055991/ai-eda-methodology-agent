// Minimal demo design for the M1 teaching demo (scripts/demo_m1.py).
// It is NEVER synthesized - M1 runs no EDA tools. This file only exists so
// the demo has a real input file to hash and register.
module demo_design (
    input  wire a,
    input  wire b,
    output wire y
);
  assign y = a & b;
endmodule
