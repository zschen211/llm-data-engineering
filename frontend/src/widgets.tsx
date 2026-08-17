import { useEffect, useState, type ReactNode } from "react";
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
  return <span className="mono">{children}</span>;
}

export function Dot({ tone }: { tone: "ok" | "warn" | "danger" | "muted" }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

export function statusTone(status: string): "ok" | "warn" | "danger" | "muted" {
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

export function Status({ status }: { status: string }) {
  return (
    <span className="status">
      <Dot tone={statusTone(status)} />
      {status}
    </span>
  );
}

export function Btn({
  children,
  onClick,
  tone = "default",
  disabled,
  title,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger";
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={`btn btn-${tone} ${className ?? ""}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
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
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <Btn onClick={onClose} title="关闭">
            ×
          </Btn>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

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
  const cls = `field ${mono ? "field-mono" : ""}`;
  const common = {
    value,
    placeholder,
    onChange: (e: { target: { value: string } }) => onChange(e.target.value),
  };
  return (
    <label className={cls}>
      <span className="field-label">{label}</span>
      {kind === "select" ? (
        <select {...common}>
          <option value="">（选择）</option>
          {(options ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : kind === "textarea" ? (
        <textarea rows={4} {...common} />
      ) : (
        <input type="text" {...common} />
      )}
    </label>
  );
}

export function Toast({ kind, message }: { kind: "ok" | "error"; message: string }) {
  return <div className={`toast toast-${kind}`}>{message}</div>;
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) {
    return null;
  }
  return <div className="note note-error">✕ {message}</div>;
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <div className="note note-empty">{children}</div>;
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
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
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} style={col.width ? { width: col.width } : undefined}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col.key}>
                  {col.render ? col.render(row) : col.mono ? (
                    <Mono>{String((row as Record<string, unknown>)[col.key] ?? "")}</Mono>
                  ) : (
                    String((row as Record<string, unknown>)[col.key] ?? "")
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
