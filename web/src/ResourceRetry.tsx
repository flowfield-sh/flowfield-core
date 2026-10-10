import { Button } from "@/components/ui/button";

// Keep recovery beside the affected content; request explanations live in Sonner.
export function ResourceRetry({
  resources,
  children = "Retry loading",
}: {
  resources: { error: string; loading?: boolean; retry: () => void }[];
  children?: React.ReactNode;
}) {
  const failed = resources.filter((resource) => resource.error);
  if (!failed.length) return null;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={failed.some((resource) => resource.loading)}
      onClick={() => failed.forEach((resource) => resource.retry())}
    >
      {children}
    </Button>
  );
}
