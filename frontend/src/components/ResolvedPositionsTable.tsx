import './ResolvedPositionsTable.css';

type Row = {
  id: number;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  status: string;
};

export function ResolvedPositionsTable({ rows }: { rows: Row[] }) {
  if (rows.length === 0) {
    return <div className="wl-resolved-empty">No positions match this view.</div>;
  }
  return (
    <table className="wl-resolved">
      <thead>
        <tr><th>Trade</th><th>Underlying</th><th>Product</th><th>Qty</th><th>Status</th></tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.id}>
            <td>{r.id}</td><td>{r.underlying}</td><td>{r.product_type}</td>
            <td>{r.quantity}</td><td>{r.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
