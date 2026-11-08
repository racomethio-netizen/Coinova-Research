"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiRequestError, apiSend } from "@/lib/client-api";

/** Per-document decisions plus the overall business decision, with reasons. */
export function BusinessDecisionForm({ businessId, documents }: { businessId: string; documents: { id: string; type: string; clean: boolean }[] }) {
  const router = useRouter();
  const base = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  return (
    <form
      className="mt-3 space-y-4"
      data-testid="business-decision-form"
      onSubmit={async (e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        const docs = documents.map((d) => {
          const status = String(f.get(`doc-${d.id}`));
          const reason = String(f.get(`reason-${d.id}`) ?? "").trim();
          return { documentId: d.id, status, ...(reason ? { reason } : {}) };
        });
        setBusy(true);
        setError(null);
        try {
          const r = await apiSend<{ status: string; outcome: string | null }>("POST", `/admin/businesses/${businessId}/decision`, {
            decision: f.get("decision"),
            reason: f.get("reason"),
            riskLevel: f.get("riskLevel"),
            documents: docs,
          });
          setResult(r.status === "PENDING_SECOND_REVIEW" ? "Recorded. A second reviewer must confirm this high-risk decision." : `Decision recorded: ${r.outcome}.`);
          router.refresh();
        } catch (err) {
          setError(err instanceof ApiRequestError ? err.message : "Request failed");
        } finally {
          setBusy(false);
        }
      }}
    >
      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold">Documents</legend>
        {documents.map((d) => (
          <div key={d.id} className="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-2">
            <div>
              <Label htmlFor={`${base}-${d.id}`}>{d.type.replace(/_/g, " ").toLowerCase()}</Label>
              <Select id={`${base}-${d.id}`} name={`doc-${d.id}`} defaultValue={d.clean ? "APPROVED" : "NEEDS_RESUBMISSION"}>
                {d.clean ? <option value="APPROVED">Approve</option> : null}
                <option value="NEEDS_RESUBMISSION">Needs resubmission</option>
                <option value="REJECTED">Reject</option>
              </Select>
            </div>
            <div>
              <Label htmlFor={`${base}-${d.id}-r`}>Reason (required if not approved)</Label>
              <Input id={`${base}-${d.id}-r`} name={`reason-${d.id}`} />
            </div>
          </div>
        ))}
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor={`${base}-decision`}>Decision</Label>
          <Select id={`${base}-decision`} name="decision" defaultValue="APPROVE">
            <option value="APPROVE">Approve business (issue Level 3)</option>
            <option value="REQUEST_CHANGES">Request changes</option>
            <option value="REJECT">Reject</option>
          </Select>
        </div>
        <div>
          <Label htmlFor={`${base}-risk`}>Risk</Label>
          <Select id={`${base}-risk`} name="riskLevel" defaultValue="LOW">
            <option value="LOW">Low</option>
            <option value="MEDIUM">Medium</option>
            <option value="HIGH">High (needs a second reviewer)</option>
          </Select>
        </div>
      </div>
      <div>
        <Label htmlFor={`${base}-reason`}>Reason (shown to the seller if not approved)</Label>
        <textarea id={`${base}-reason`} name="reason" required minLength={3} rows={3} className="block w-full rounded-md border border-line px-3 py-2" />
      </div>
      {error ? <Alert tone="error">{error}</Alert> : null}
      {result ? <Alert tone="success">{result}</Alert> : null}
      <Button type="submit" disabled={busy}>Record decision</Button>
    </form>
  );
}
