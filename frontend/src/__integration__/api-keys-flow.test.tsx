import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

function getParentRow(cell: HTMLElement): HTMLElement {
  const row = cell.closest<HTMLElement>(".row");
  if (!row) throw new Error("Expected element to be inside a key row");
  return row;
}

async function openRowActions(user: ReturnType<typeof userEvent.setup>, row: HTMLElement) {
  const actionsButton = within(row).getByRole("button", { name: /Actions for/ });
  await user.click(actionsButton);
}

describe("api keys flow integration", () => {
  it("creates, shows plain key dialog, edits, and deletes an api key", async () => {
    const user = userEvent.setup();
    const createdName = "Integration Key";
    const updatedName = "Integration Key Updated";

    window.history.pushState({}, "", "/keys");
    renderWithProviders(<App />);

    const createButton = await screen.findByRole("button", { name: "New key" });
    expect(createButton).toBeInTheDocument();
    await user.click(createButton);
    await user.click(await screen.findByRole("button", { name: /You.*personal key/ }));
    await user.type(screen.getByLabelText("Name"), createdName);
    await user.click(screen.getByRole("button", { name: "Create" }));

    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });
    expect(screen.getByText(/sk-test-generated/i)).toBeInTheDocument();
    const closeCandidates = within(createdDialog).getAllByRole("button", {
      name: "Close",
    });
    const closeButton =
      closeCandidates.find((element) => element.getAttribute("data-slot") === "button") ??
      closeCandidates[0];
    await user.click(closeButton);

    const createdRow = getParentRow(await screen.findByText(createdName));

    await openRowActions(user, createdRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));
    const nameInput = await screen.findByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, updatedName);
    await user.click(screen.getByRole("button", { name: "Save" }));

    const updatedRow = getParentRow(await screen.findByText(updatedName));

    await openRowActions(user, updatedRow);
    await user.click(await screen.findByRole("menuitem", { name: "Revoke" }));
    const confirmTitle = await screen.findByText(`Revoke ${updatedName}?`);
    const confirmDialog = confirmTitle.closest("[role='alertdialog']");
    expect(confirmDialog).not.toBeNull();
    if (!confirmDialog) throw new Error("Expected confirm dialog");
    await user.click(within(confirmDialog as HTMLElement).getByRole("button", { name: "Revoke" }));

    await waitFor(() => {
      expect(screen.queryByText(updatedName)).not.toBeInTheDocument();
    });
  });

  it("creates an api key with assigned accounts", async () => {
    const user = userEvent.setup();

    window.history.pushState({}, "", "/keys");
    renderWithProviders(<App />);

    await user.click(await screen.findByRole("button", { name: "New key" }));
    await user.click(await screen.findByRole("button", { name: /You.*personal key/ }));
    await user.type(screen.getByLabelText("Name"), "Scoped Integration Key");
    await user.click(await screen.findByRole("button", { name: "All accounts" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Create" }));

    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });
    const closeCandidates = within(createdDialog).getAllByRole("button", {
      name: "Close",
    });
    const closeButton =
      closeCandidates.find((element) => element.getAttribute("data-slot") === "button") ??
      closeCandidates[0];
    await user.click(closeButton);

    const createdRow = getParentRow(await screen.findByText("Scoped Integration Key"));
    await openRowActions(user, createdRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));

    expect(await screen.findByRole("button", { name: "1 account selected" })).toBeInTheDocument();
  });

  it("displays personal and revoked keys in the cockpit", async () => {
    const user = userEvent.setup({ delay: null });
    window.history.pushState({}, "", "/keys");
    renderWithProviders(<App />);

    const defaultKey = await screen.findByRole("link", { name: /Default key/ });
    const defaultKeyRow = getParentRow(defaultKey);
    expect(within(defaultKeyRow).getByText("$1.23")).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Read only key/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Show 1 key revoked/ }));
    expect(await screen.findByRole("link", { name: /Read only key/ })).toBeInTheDocument();
  });

  it("shows usage bars when editing a key with limits", async () => {
    const user = userEvent.setup({ delay: null });

    window.history.pushState({}, "", "/keys");
    renderWithProviders(<App />);

    expect(await screen.findByText("Default key")).toBeInTheDocument();
    const defaultKeyRow = getParentRow(screen.getByText("Default key"));
    await openRowActions(user, defaultKeyRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));

    // Edit dialog should show current usage section
    expect(await screen.findByText("Current usage")).toBeInTheDocument();
  });
});
