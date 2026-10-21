import { ButtonLink, Container } from "@/components/ui";

export default function NotFound() {
  return (
    <Container className="py-20 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-muted">The page or asset you requested does not exist, or is not in our supported coverage.</p>
      <div className="mt-6 flex justify-center gap-3">
        <ButtonLink href="/">Home</ButtonLink>
        <ButtonLink href="/markets" variant="secondary">
          Markets
        </ButtonLink>
      </div>
    </Container>
  );
}
