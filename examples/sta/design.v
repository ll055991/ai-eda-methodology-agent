// Minimal 4-bit counter for the M2.2 Docker/Yosys environment demo.
//
// This is a TOY design on purpose: no PDK, no constraints, no timing
// targets, no claim of being a real chip. It only proves that the
// environment can run real synthesis (RTL -> Yosys -> gate-level netlist).
module simple_counter #(
    parameter WIDTH = 4
) (
    input  wire             clk,
    input  wire             rst_n,
    output reg  [WIDTH-1:0] count
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= {WIDTH{1'b0}};
        else
            count <= count + 1'b1;
    end
endmodule
