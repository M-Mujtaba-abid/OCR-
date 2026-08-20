"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  useApprovalChains,
  useDeleteChain,
  useSaveChain,
  useSetChainActive,
} from "@/hooks/approval/useApprovals.hooks";
import { useUsers } from "@/hooks/user/useUsers.hooks";
import { ROLE_LABEL } from "@/lib/auth/roles";
import type { ApprovalChain } from "@/types/approval.type";
import type { User } from "@/types/user.type";

/**
 * A step being edited.
 *
 * No `position`: they are assigned from array order on save, and a position
 * carried in the form would be a second source of truth for something the order
 * already says.
 */
interface DraftStep {
  name: string;
  approver_user_ids: string[];
}

/**
 * Who has to approve a vendor bill, and in what order.
 *
 * The screen that makes the rest of the feature reachable — without it a chain
 * can only be created by calling the API by hand.
 *
 * Activating is treated as the consequential act it is. From the moment a chain
 * is active, no vendor bill in the company can be raised until every step on it
 * has been decided, so it is a separate confirmed action rather than a checkbox
 * that rides along with saving a name.
 */
export function ApprovalsPanel() {
  const chains = useApprovalChains();
  // One page of the company's people, which is who a step can name. Big enough
  // that a business of normal size never meets the limit, and paginating a
  // checkbox list would make "who is on this step" unanswerable.
  const users = useUsers({ pageSize: 100 });
  const [editing, setEditing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const people = (users.data?.items ?? []).filter((person) => person.is_active);

  if (chains.isLoading) {
    return <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>;
  }

  if (chains.isError) {
    return (
      <Alert variant="error">
        The approval chains could not be loaded. Refresh to try again.
      </Alert>
    );
  }

  const list = chains.data ?? [];
  const active = list.find((chain) => chain.is_active);

  return (
    <div className="space-y-5">
      {active ? (
        <Alert variant="success">
          <span className="font-medium">{active.name}</span> is gating vendor
          bills. An invoice cannot be billed until all {active.steps.length}{" "}
          {active.steps.length === 1 ? "step" : "steps"} have been decided.
        </Alert>
      ) : (
        <Alert variant="info">
          No chain is active, so vendor bills are raised exactly as they were
          before approvals existed. Activating one starts gating every bill in
          this company.
        </Alert>
      )}

      {list.map((chain) =>
        editing === chain.id ? (
          <ChainEditor
            key={chain.id}
            chain={chain}
            people={people}
            onDone={() => setEditing(null)}
          />
        ) : (
          <ChainCard
            key={chain.id}
            chain={chain}
            people={people}
            onEdit={() => setEditing(chain.id)}
          />
        ),
      )}

      {creating ? (
        <ChainEditor people={people} onDone={() => setCreating(false)} />
      ) : (
        <Button variant="secondary" onClick={() => setCreating(true)}>
          New chain…
        </Button>
      )}
    </div>
  );
}

function nameOf(people: User[], id: string): string {
  const person = people.find((candidate) => candidate.id === id);
  // Somebody named on a step who is no longer an active user of this company.
  // Said plainly rather than shown as a bare id: the chain cannot be activated
  // while they are on it, and the admin needs to know which row to fix.
  if (!person) return "(no longer active)";
  return person.full_name?.trim() || person.email;
}

function ChainCard({
  chain,
  people,
  onEdit,
}: {
  chain: ApprovalChain;
  people: User[];
  onEdit: () => void;
}) {
  const setActive = useSetChainActive();
  const remove = useDeleteChain();
  const [confirming, setConfirming] = useState(false);

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
          {chain.name}
        </h3>
        <Badge tone={chain.is_active ? "positive" : "neutral"}>
          {chain.is_active ? "Gating bills" : "Not in use"}
        </Badge>
      </div>

      <ol className="mt-4 space-y-2">
        {chain.steps.map((step) => (
          <li key={step.position} className="text-sm">
            <span className="font-medium text-slate-900 dark:text-white">
              {step.position}. {step.name}
            </span>
            <span className="ml-2 text-xs text-slate-600 dark:text-slate-400">
              {step.approver_user_ids.map((id) => nameOf(people, id)).join(", ")}
              {step.approver_user_ids.length > 1 && " — any one of them"}
            </span>
          </li>
        ))}
      </ol>

      {chain.allow_self_approval && (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
          Self-approval is allowed on this chain: whoever asks for the bill may
          also approve it. Only right where one person genuinely is the whole
          approval process.
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-3 border-t border-slate-200 pt-5 dark:border-slate-800">
        <Button variant="secondary" onClick={onEdit}>
          Edit
        </Button>

        {chain.is_active ? (
          <Button
            variant="secondary"
            isLoading={setActive.isPending}
            onClick={() => setActive.mutate({ chainId: chain.id, active: false })}
          >
            Stop gating bills
          </Button>
        ) : (
          !confirming && (
            <>
              <Button onClick={() => setConfirming(true)}>Activate…</Button>
              {/* Offered only on a chain that is not gating anything. The
                  server refuses the other cases too, with the reason; not
                  rendering the button here keeps the obvious mistake from
                  needing a round trip to explain. */}
              <Button
                variant="ghost"
                isLoading={remove.isPending}
                onClick={() => remove.mutate(chain.id)}
              >
                Delete
              </Button>
            </>
          )
        )}
      </div>

      {confirming && !chain.is_active && (
        <div className="mt-4 space-y-3">
          <Alert variant="info">
            From now on, no vendor bill in this company can be raised until this
            chain has been completed for that invoice. Any chain currently active
            is stood down — only one can gate bills at a time.
          </Alert>
          <div className="flex flex-wrap gap-3">
            <Button
              isLoading={setActive.isPending}
              onClick={() =>
                setActive.mutate(
                  { chainId: chain.id, active: true },
                  { onSuccess: () => setConfirming(false) },
                )
              }
            >
              Activate {chain.name}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Not yet
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function ChainEditor({
  chain,
  people,
  onDone,
}: {
  chain?: ApprovalChain;
  people: User[];
  onDone: () => void;
}) {
  const save = useSaveChain();
  const [name, setName] = useState(chain?.name ?? "");
  const [allowSelf, setAllowSelf] = useState(chain?.allow_self_approval ?? false);
  const [steps, setSteps] = useState<DraftStep[]>(
    chain?.steps.map((step) => ({
      name: step.name,
      // Filtered to people who are still here. Carrying a departed approver
      // through an edit would make the chain unsavable with an error about a
      // row the admin never touched.
      approver_user_ids: step.approver_user_ids.filter((id) =>
        people.some((person) => person.id === id),
      ),
    })) ?? [{ name: "", approver_user_ids: [] }],
  );

  const patch = (index: number, next: Partial<DraftStep>) =>
    setSteps((current) =>
      current.map((step, i) => (i === index ? { ...step, ...next } : step)),
    );

  const move = (index: number, by: number) =>
    setSteps((current) => {
      const target = index + by;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });

  // Mirrors what the server refuses, so the common mistakes are caught without
  // a round trip. It is not the authority: whether an approver is still an
  // active user of this company is only knowable there.
  const valid =
    name.trim().length > 0 &&
    steps.length > 0 &&
    steps.every(
      (step) => step.name.trim().length > 0 && step.approver_user_ids.length > 0,
    );

  return (
    <section className="rounded-xl border border-slate-300 bg-white p-6 dark:border-slate-700 dark:bg-slate-900">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
        {chain ? `Edit ${chain.name}` : "New chain"}
      </h3>

      <label className="mt-4 block">
        <span className="text-xs font-medium text-slate-700 dark:text-slate-300">
          Name
        </span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={120}
          placeholder="Vendor bill approval"
          className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-300 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
        />
      </label>

      <div className="mt-5 space-y-4">
        {steps.map((step, index) => (
          <div
            key={index}
            className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">
                Step {index + 1}
              </span>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                  aria-label={`Move step ${index + 1} earlier`}
                >
                  Up
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={index === steps.length - 1}
                  onClick={() => move(index, 1)}
                  aria-label={`Move step ${index + 1} later`}
                >
                  Down
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  // A chain needs at least one step and the server refuses one
                  // with none, so the control that would create that state is
                  // simply not offered.
                  disabled={steps.length === 1}
                  onClick={() =>
                    setSteps((current) => current.filter((_, i) => i !== index))
                  }
                >
                  Remove
                </Button>
              </div>
            </div>

            <input
              value={step.name}
              onChange={(event) => patch(index, { name: event.target.value })}
              maxLength={120}
              placeholder="What this step is — e.g. Goods received"
              className="mt-2 w-full rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-300 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
            />

            <p className="mt-3 text-xs font-medium text-slate-700 dark:text-slate-300">
              Who can decide it — any one of them
            </p>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-500">
              Name more than one person wherever you can. A step with a single
              approver stops every invoice that reaches it the week they are on
              leave.
            </p>

            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {people.map((person) => {
                const checked = step.approver_user_ids.includes(person.id);
                return (
                  <label
                    key={person.id}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        patch(index, {
                          approver_user_ids: checked
                            ? step.approver_user_ids.filter(
                                (id) => id !== person.id,
                              )
                            : [...step.approver_user_ids, person.id],
                        })
                      }
                      className="size-4 rounded border-slate-300 dark:border-slate-600"
                    />
                    <span className="truncate">
                      {person.full_name?.trim() || person.email}
                    </span>
                    <span className="ml-auto shrink-0 text-xs text-slate-500">
                      {ROLE_LABEL[person.role]}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4">
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            setSteps((current) => [
              ...current,
              { name: "", approver_user_ids: [] },
            ])
          }
        >
          Add a step
        </Button>
      </div>

      <label className="mt-5 flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
        <input
          type="checkbox"
          checked={allowSelf}
          onChange={(event) => setAllowSelf(event.target.checked)}
          className="mt-0.5 size-4 rounded border-slate-300 dark:border-slate-600"
        />
        <span>
          Let whoever asks for the bill also approve it.
          <span className="block text-xs text-slate-500 dark:text-slate-500">
            Off is right for almost everybody — the point of a chain is a second
            pair of eyes. Turn it on only where one person genuinely is the whole
            approval process, or their own steps can never be decided.
          </span>
        </span>
      </label>

      <div className="mt-5 flex flex-wrap gap-3 border-t border-slate-200 pt-5 dark:border-slate-800">
        <Button
          disabled={!valid}
          isLoading={save.isPending}
          onClick={() =>
            save.mutate(
              {
                chainId: chain?.id,
                input: {
                  name: name.trim(),
                  allow_self_approval: allowSelf,
                  // Saving never changes whether a chain is gating. Turning one
                  // on stops every bill in the company until each step is
                  // decided, which is not something to do as a side effect of
                  // fixing a typo in a step name.
                  is_active: chain?.is_active ?? false,
                  steps: steps.map((step) => ({
                    name: step.name.trim(),
                    approver_user_ids: step.approver_user_ids,
                  })),
                },
              },
              { onSuccess: onDone },
            )
          }
        >
          Save
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
