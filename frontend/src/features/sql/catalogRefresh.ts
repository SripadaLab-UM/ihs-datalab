import type { QueryClient } from "@tanstack/react-query";

/** After the knowledge base synced, DataLab reads its table catalog again (backend
 *  catalog_source.py): everything showing the catalog asks again. */
export function refreshCatalog(queryClient: QueryClient) {
  for (const key of ["health", "catalog-status", "sql-catalog", "sql-catalog-search"])
    void queryClient.invalidateQueries({ queryKey: [key] });
}
