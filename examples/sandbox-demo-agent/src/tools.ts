/** The shop. A real refund prints a line, so you can see that a sandbox run never prints it. */
type Order = { order_id: string; item: string; total_usd: number; delivered_days_ago: number };

const ORDERS: Record<string, Order> = {
  A100: { order_id: "A100", item: "Desk lamp", total_usd: 39, delivered_days_ago: 4 },
  A200: { order_id: "A200", item: "Ceramic vase", total_usd: 120, delivered_days_ago: 95 },
};

export function lookupOrder({ order_id }: { order_id: string }): Order | { error: string } {
  return ORDERS[order_id] ?? { error: `There is no order ${order_id}.` };
}

export function refundOrder({ order_id }: { order_id: string }) {
  console.log(`REAL REFUND of ${order_id}`);
  return { order_id, status: "refunded" };
}
