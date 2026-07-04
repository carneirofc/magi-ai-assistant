"use client";

// The knowledge document list with filters: subject is a hard filter (a row must
// match), tags are soft (matching rows float to the top but none are hidden) —
// mirroring how the model's search_knowledge treats them. A table/grid toggle
// switches between a dense list and mem0-style memory cards.

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  EmptyState,
  InfoChip,
  SegmentedControl,
  SelectInput,
  SurfacePanel,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
} from "@carneirofc/ui";

import { encodeDocId } from "../lib/encode";

type Doc = {
  doc_id: string;
  source: string;
  title: string;
  subject: string;
  tags: string[];
  chunk_count: number;
  latest_ts: string;
};

type View = "table" | "grid";

export function KnowledgeList({
  documents,
  subjects,
}: {
  documents: Doc[];
  subjects: string[];
}) {
  const [subject, setSubject] = useState("");
  const [tag, setTag] = useState("");
  const [view, setView] = useState<View>("table");

  const rows = useMemo(() => {
    const filtered = subject ? documents.filter((d) => d.subject === subject) : [...documents];
    if (tag.trim()) {
      const t = tag.trim().toLowerCase();
      filtered.sort((a, b) => {
        const am = a.tags.some((x) => x.toLowerCase().includes(t)) ? 0 : 1;
        const bm = b.tags.some((x) => x.toLowerCase().includes(t)) ? 0 : 1;
        return am - bm;
      });
    }
    return filtered;
  }, [documents, subject, tag]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Subject</span>
            <SelectInput
              controlSize="sm"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            >
              <option value="">all</option>
              {subjects.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </SelectInput>
          </label>
          <label className="flex flex-col gap-1">
            <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Tag bias</span>
            <TextInput
              controlSize="sm"
              aria-label="Filter by tag"
              placeholder="tag bias…"
              value={tag}
              onChange={(e) => setTag(e.target.value)}
            />
          </label>
        </div>
        <SegmentedControl<View>
          value={view}
          onValueChange={setView}
          options={[
            { value: "table", label: "Table" },
            { value: "grid", label: "Cards" },
          ]}
        />
      </div>

      {rows.length === 0 ? (
        <EmptyState>No documents match.</EmptyState>
      ) : view === "table" ? (
        <SurfacePanel tone="soft" padding="none" className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Document</TableHead>
                <TableHead>Subject</TableHead>
                <TableHead>Tags</TableHead>
                <TableHead className="text-right">Chunks</TableHead>
                <TableHead>Last ingested</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((d) => (
                <TableRow key={d.doc_id}>
                  <TableCell>
                    <Link href={`/knowledge/${encodeDocId(d.doc_id)}`} className="font-medium">
                      {d.title || d.source || d.doc_id}
                    </Link>
                    <div className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">{d.doc_id}</div>
                  </TableCell>
                  <TableCell>
                    {d.subject ? (
                      <InfoChip>{d.subject}</InfoChip>
                    ) : (
                      <span className="text-[color:var(--ui-ink-subtle)]">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="text-[color:var(--ui-ink-muted)]">
                      {d.tags.join(", ") || "—"}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{d.chunk_count}</TableCell>
                  <TableCell className="text-[color:var(--ui-ink-subtle)]">{d.latest_ts}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </SurfacePanel>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((d) => (
            <Link key={d.doc_id} href={`/knowledge/${encodeDocId(d.doc_id)}`} className="no-underline">
              <SurfacePanel
                tone="soft"
                padding="md"
                className="flex h-full flex-col gap-2 transition-colors hover:border-ui-active"
              >
                <p className="text-ui-sm font-semibold text-[color:var(--ui-ink)]">
                  {d.title || d.source || d.doc_id}
                </p>
                <p className="truncate text-ui-2xs text-[color:var(--ui-ink-subtle)]">{d.doc_id}</p>
                <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-1">
                  {d.subject ? <InfoChip>{d.subject}</InfoChip> : null}
                  {d.tags.slice(0, 3).map((t) => (
                    <InfoChip key={t}>{t}</InfoChip>
                  ))}
                </div>
                <p className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">
                  {d.chunk_count} chunks · {d.latest_ts}
                </p>
              </SurfacePanel>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
