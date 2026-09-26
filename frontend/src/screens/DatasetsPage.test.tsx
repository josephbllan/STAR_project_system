import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { DatasetsPage } from "./DatasetsPage";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: vi.fn(async (path: string) => {
      if (path === "/api/v1/corpora/") {
        return { results: [{ id: 1, code: "Ecom", name: "Ecom", description: "", data_classification: "synthetic", is_active: true }] };
      }
      if (path === "/api/v1/mounts/") {
        return { results: [] };
      }
      return {};
    }),
  };
});

describe("DatasetsPage", () => {
  it("offers Add Folder for any corpus", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatasetsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "Add Folder" })).toBeInTheDocument();
  });
});
