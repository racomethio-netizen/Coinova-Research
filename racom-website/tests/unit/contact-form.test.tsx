// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContactForm } from "@/components/contact/contact-form";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/^Name/), "Abebe Kebede");
  await user.type(screen.getByLabelText(/^Work email/), "abebe@example.com");
  await user.selectOptions(screen.getByLabelText(/^Service needed/), "ai-automation");
  await user.type(
    screen.getByLabelText(/^Project description/),
    "We would like to automate how incoming support requests are routed.",
  );
  await user.click(screen.getByLabelText(/I agree/));
}

function mockFetch(status: number, body: unknown) {
  const fn = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
    );
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("ContactForm", () => {
  it("shows accessible validation errors and does not submit invalid input", async () => {
    const fetchMock = mockFetch(200, {});
    const user = userEvent.setup();
    render(<ContactForm contactEmail={null} />);
    await user.click(screen.getByRole("button", { name: "Send inquiry" }));

    const name = screen.getByLabelText(/^Name/);
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("Please enter your name.");
    expect(screen.getByText("Please choose the service you need.")).toBeInTheDocument();
    expect(screen.getByText("Please confirm so we can use these details to reply.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the success state only after the server confirms delivery", async () => {
    const fetchMock = mockFetch(200, { ok: true, delivery: "email", reference: "ref-123" });
    const user = userEvent.setup();
    render(<ContactForm contactEmail={null} />);
    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: "Send inquiry" }));

    expect(await screen.findByText("Thank you — your inquiry was sent.")).toBeInTheDocument();
    expect(screen.getByText(/ref-123/)).toBeInTheDocument();
    const sent = JSON.parse(fetchMock.mock.calls[0]![1].body);
    expect(sent).toMatchObject({
      name: "Abebe Kebede",
      service: "ai-automation",
      consent: true,
      website: "",
    });
    expect(typeof sent.startedAt).toBe("number");
  });

  it("states clearly when an inquiry was only saved locally (development)", async () => {
    mockFetch(200, { ok: true, delivery: "local", reference: "ref-dev" });
    const user = userEvent.setup();
    render(<ContactForm contactEmail={null} />);
    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: "Send inquiry" }));
    expect(await screen.findByText("Saved locally (development mode)")).toBeInTheDocument();
    expect(screen.getByText("not emailed")).toBeInTheDocument();
  });

  it("shows a failure state — never success — when delivery fails", async () => {
    mockFetch(503, {
      ok: false,
      error: { code: "not_configured", message: "Online inquiries are temporarily unavailable." },
    });
    const user = userEvent.setup();
    render(<ContactForm contactEmail="hello@racom.test" />);
    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: "Send inquiry" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your inquiry was not sent.");
    expect(alert).toHaveTextContent("Online inquiries are temporarily unavailable.");
    expect(alert).toHaveTextContent("hello@racom.test");
    expect(screen.queryByText(/Thank you/)).not.toBeInTheDocument();
    // Entered data is kept so the user can retry.
    expect(screen.getByLabelText(/^Name/)).toHaveValue("Abebe Kebede");
  });

  it("maps server-side field errors onto the fields", async () => {
    mockFetch(422, {
      ok: false,
      error: {
        code: "validation_error",
        message: "Please check the highlighted fields.",
        fields: { email: "Please enter a valid email address." },
      },
    });
    const user = userEvent.setup();
    render(<ContactForm contactEmail={null} />);
    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: "Send inquiry" }));
    await waitFor(() => expect(screen.getByLabelText(/^Work email/)).toHaveAttribute("aria-invalid", "true"));
  });

  it("handles network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const user = userEvent.setup();
    render(<ContactForm contactEmail={null} />);
    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: "Send inquiry" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not reach the server");
  });

  it("preselects a valid service from the URL and ignores unknown ones", () => {
    const { unmount } = render(<ContactForm contactEmail={null} defaultService="web-development" />);
    expect(screen.getByLabelText(/^Service needed/)).toHaveValue("web-development");
    unmount();
    render(<ContactForm contactEmail={null} defaultService="bogus" />);
    expect(screen.getByLabelText(/^Service needed/)).toHaveValue("");
  });
});
