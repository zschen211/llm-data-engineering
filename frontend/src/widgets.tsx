// Shared helpers on top of shadcn/ui + Tailwind. Data fetching (useFetch /
// usePoll) and formatters live here; all presentational components are
// shadcn/ui wrappers so the whole console shares one component contract.

import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table as UiTable,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { ApiError, get } from "./api";

// ---- data fetching ---------------------------------------------------------

export function useFetch<T>(path: string, refresh: number = 0): {
  data: T | null;
  error: string;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    get<T>(path)
      .then((result) => {
        if (alive) {
          setData(result);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (alive) {
          setError(err instanceof ApiError ? err.message : String(err));
        }
      });
    return () => {
      alive = false;
    };
  }, [path, tick, refresh]);

  return { data, error, reload: () => setTick((n) => n + 1) };
}

export function usePoll<T>(path: string, intervalMs: number): T | null {
  const { data } = useFetch<T>(path, intervalMs);
  return data;
}

// ---- primitives ------------------------------------------------------------

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono">{children}</span>;
}

export function Dot({ tone }: { tone: "ok" | "warn" | "danger" | "muted" }) {
  return (
    <span
      className={cn(
        "mr-1.5 inline-block size-[7px] rounded-full align-[1px]",
        tone === "ok" && "bg-success",
        tone === "warn" && "bg-warning",
        tone === "danger" && "bg-destructive",
        tone === "muted" && "bg-border",
      )}
      aria-hidden="true"
    />
  );
}

export type StatusTone = "ok" | "warn" | "danger" | "muted";

export function statusTone(status: string): StatusTone {
  const s = String(status).toLowerCase();
  if (["ready", "succeeded", "done", "completed", "persisted", "uploaded", "new"].includes(s)) {
    return "ok";
  }
  if (["running", "syncing", "downloading", "pending", "queued"].includes(s)) {
    return "warn";
  }
  if (["failed", "error", "cancelled", "deleted", "missing"].includes(s)) {
    return "danger";
  }
  return "muted";
}

const statusBadgeClass: Record<StatusTone, string> = {
  ok: "border-transparent bg-success-soft text-success",
  warn: "border-transparent bg-warning-soft text-warning",
  danger: "border-transparent bg-destructive/10 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

export function Status({ status }: { status: string }) {
  const tone = statusTone(status);
  return <Badge className={cn("font-mono", statusBadgeClass[tone])}>{status}</Badge>;
}

// ---- page templates ---------------------------------------------------------

// The normalization layer: every page follows the same skeleton —
// PageContainer (desc/actions) → optional Tabs → DataTable (toolbar/table/
// footer) with CursorPagination, and FormModal for create/edit dialogs.
// The sidebar already carries the section/page context, so the page itself
// renders no title header — only the description and the action buttons.

// Typographic scale (mature-admin conventions, one source of truth):
//   text-xs (12px)  meta — labels, table headers, badges, strip tags
//   text-sm (14px)  body — descriptions, table cells, buttons, forms
//   text-base (16px) block titles — section headers, modal titles
//   text-xl (20px)  hero numbers — stat card values
// Mono is reserved for data (ids/hashes/timestamps) and meta labels.

export function PageContainer({
  desc,
  actions,
  children,
}: {
  desc?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      {(desc || actions) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          {desc && <p className="m-0 text-sm text-muted-foreground">{desc}</p>}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageSection({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="mt-5 border-t-2 border-border pt-3.5">
      <div className="mb-2 flex items-baseline gap-3">
        <span className="font-mono text-xs uppercase tracking-widest text-primary">{eyebrow}</span>
        <h2 className="m-0 font-mono text-base font-semibold">{title}</h2>
      </div>
      {children}
    </div>
  );
}

export function DataTable<T>({
  columns,
  rows,
  toolbar,
  footer,
}: {
  columns: Column<T>[];
  rows: T[];
  toolbar?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div>
      {toolbar && <div className="mb-3 flex flex-wrap items-center gap-2">{toolbar}</div>}
      <Table columns={columns} rows={rows} />
      {footer && <div className="mt-3 flex flex-wrap items-center gap-2">{footer}</div>}
    </div>
  );
}

export function CursorPagination({
  cursor,
  nextCursor,
  total,
  onFirst,
  onNext,
}: {
  cursor?: string;
  nextCursor?: string | null;
  total?: number;
  onFirst: () => void;
  onNext: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="outline" disabled={!cursor} onClick={onFirst}>
        首页
      </Button>
      <Button size="sm" variant="outline" disabled={!nextCursor} onClick={onNext}>
        下一页
      </Button>
      {total != null && (
        <span className="ml-2 font-mono text-xs text-muted-foreground">共 {total} 条</span>
      )}
    </div>
  );
}

export function FormModal({
  title,
  onClose,
  onConfirm,
  confirmLabel = "保存",
  saving,
  error,
  children,
}: {
  title: string;
  onClose: () => void;
  onConfirm: () => void;
  confirmLabel?: string;
  saving?: boolean;
  error?: string;
  children: ReactNode;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      {children}
      <ErrorNote message={error ?? ""} />
      <div className="flex gap-2">
        <Button onClick={onConfirm} disabled={saving}>
          {saving ? "保存中…" : confirmLabel}
        </Button>
        <Button variant="outline" onClick={onClose}>
          取消
        </Button>
      </div>
    </Modal>
  );
}

// ---- overview blocks -------------------------------------------------------

export function StatCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border bg-card p-3.5">
      <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 font-mono text-xl font-semibold">{value}</div>
    </div>
  );
}

export function FieldItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-md border bg-card px-3 py-2">
      <span className="mb-0.5 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  );
}

// ---- forms & modal ---------------------------------------------------------

export function Field({
  label,
  value,
  onChange,
  placeholder,
  kind = "text",
  options,
  mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  kind?: "text" | "select" | "textarea";
  options?: string[];
  mono?: boolean;
}) {
  const labelCls = "font-mono text-xs tracking-wider text-muted-foreground";
  if (kind === "select") {
    return (
      <div className="flex flex-col gap-1.5">
        <Label className={labelCls}>{label}</Label>
        <Select value={value || undefined} onValueChange={onChange}>
          <SelectTrigger className="w-fit min-w-40">
            <SelectValue placeholder={placeholder ?? "（选择）"} />
          </SelectTrigger>
          <SelectContent>
            {(options ?? []).map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }
  if (kind === "textarea") {
    return (
      <div className="flex flex-col gap-1.5">
        <Label className={labelCls}>{label}</Label>
        <Textarea
          rows={4}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={mono ? "font-mono text-xs" : "font-sans text-sm"}
        />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Label className={labelCls}>{label}</Label>
      <Input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn("h-9", mono ? "font-mono text-xs" : "font-sans text-sm")}
      />
    </div>
  );
}

export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) {
          onClose();
        }
      }}
    >
      <DialogContent className="flex max-h-[82vh] w-[min(560px,100%)] flex-col gap-4">
        <DialogHeader className="border-b pb-3">
          <DialogTitle className="font-mono text-base">{title}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3 overflow-y-auto">{children}</div>
      </DialogContent>
    </Dialog>
  );
}

// ---- notes ----------------------------------------------------------------

export function ErrorNote({ message }: { message: string }) {
  if (!message) {
    return null;
  }
  return (
    <Alert variant="destructive" className="mb-3">
      <AlertTitle className="font-mono text-xs">请求失败</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-sm text-muted-foreground">{children}</div>;
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-[60vh] overflow-x-auto rounded-md bg-[#10182a] p-4 font-mono text-xs leading-relaxed text-[#c9d6ee]">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// ---- generic table ---------------------------------------------------------

export interface Column<T> {
  key: string;
  label: string;
  render?: (row: T) => ReactNode;
  mono?: boolean;
  width?: string;
}

export function Table<T>({ columns, rows }: { columns: Column<T>[]; rows: T[] }) {
  if (rows.length === 0) {
    return <EmptyNote>暂无数据</EmptyNote>;
  }
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <UiTable>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {columns.map((col) => (
              <TableHead
                key={col.key}
                className="font-mono text-xs uppercase tracking-wider text-muted-foreground"
                style={col.width ? { width: col.width } : undefined}
              >
                {col.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i} className="hover:bg-accent/50">
              {columns.map((col) => (
                <TableCell key={col.key}>
                  {col.render ? (
                    col.render(row)
                  ) : col.mono ? (
                    <Mono>{String((row as Record<string, unknown>)[col.key] ?? "")}</Mono>
                  ) : (
                    String((row as Record<string, unknown>)[col.key] ?? "")
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </UiTable>
    </div>
  );
}

// ---- formatters ------------------------------------------------------------

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) {
    return "";
  }
  if (n < 1024) {
    return `${n} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(1)} ${units[u]}`;
}

export function fmtTime(ts: string | null | undefined): string {
  if (!ts) {
    return "";
  }
  return ts.replace("T", " ").slice(0, 19);
}
