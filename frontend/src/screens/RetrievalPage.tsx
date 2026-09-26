import { useParams } from "react-router-dom";

export function RetrievalPage() {
  const { id } = useParams();
  return (
    <section>
      <h1 className="text-2xl font-semibold">Retrieval</h1>
      <p className="text-muted">Case {id}. Submit a query from the case run once encoders are registered.</p>
    </section>
  );
}
