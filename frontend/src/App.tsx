import { useCallback, useEffect, useState } from "react";
import { Toaster } from "sonner";
import { cn } from "@/lib/utils";
import { LayeredStrip } from "./LayeredStrip";
import { assetOverviewPage, assetPages } from "./pages/assets";
import { factoryEvalPages, factoryOverviewPage, factoryStrategyPages } from "./pages/factory";
import { infraPages } from "./pages/infra";
import "./index.css";

interface PageDef {
  key: string;
  label: string;
  num?: string;
  component: () => React.JSX.Element;
}

interface PageGroup {
  label: string;
  pages: PageDef[];
}

interface SectionDef {
  key: string;
  label: string;
  overview: PageDef;
  pages: PageDef[];
  groups?: PageGroup[];
}

const sections: SectionDef[] = [
  {
    key: "asset",
    label: "数据资产",
    overview: assetOverviewPage,
    pages: assetPages,
  },
  {
    key: "factory",
    label: "数据工厂",
    overview: factoryOverviewPage,
    pages: [],
    groups: [
      { label: "数据策略", pages: factoryStrategyPages },
      { label: "模型评测", pages: factoryEvalPages },
    ],
  },
  {
    key: "infra",
    label: "基础设施",
    overview: infraPages[0],
    pages: infraPages.slice(1),
  },
];

const numbered = (pages: PageDef[]): PageDef[] =>
  pages.map((p, i) => ({ ...p, num: `${i + 1}`.padStart(2, "0") }));

export default function App() {
  const [hash, setHash] = useState(() => window.location.hash || "#/asset");

  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/asset");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: string) => {
    window.location.hash = next;
  }, []);

  const resolved: SectionDef[] = sections.map((s) => ({
    ...s,
    overview: { ...s.overview, num: "00" },
    pages: numbered(s.pages),
    groups: s.groups?.map((g) => ({ ...g, pages: numbered(g.pages) })),
  }));

  const parts = hash.slice(2).split("/");
  const section = resolved.find((s) => s.key === parts[0]) ?? resolved[0];
  const allPages = [...section.pages, ...(section.groups ?? []).flatMap((g) => g.pages)];
  const page = allPages.find((p) => p.key === parts[1]) ?? section.overview;
  const active = page === section.overview ? "overview" : page.key;

  const renderLink = (p: PageDef) => (
    <a
      key={p.key}
      className={cn(
        "block rounded px-3 py-1.5 text-sm no-underline text-foreground hover:bg-accent",
        active === p.key && "bg-primary text-primary-foreground hover:bg-primary",
      )}
      href={`#/${section.key}/${p.key}`}
    >
      <span
        className={cn(
          "mr-2 font-mono text-xs text-muted-foreground",
          active === p.key && "text-primary-foreground/70",
        )}
      >
        {p.num}
      </span>
      {p.label}
    </a>
  );

  const renderSection = (s: SectionDef) => (
    <div className="mb-1.5" key={s.key}>
      <a
        className={cn(
          "mb-1 mt-3.5 block rounded-md border-l-2 border-primary bg-accent px-3 py-1.5 font-mono text-xs font-semibold tracking-widest no-underline text-foreground hover:bg-accent/70",
          section.key === s.key && active === "overview" && "bg-primary text-primary-foreground hover:bg-primary",
        )}
        href={`#/${s.key}`}
      >
        {s.label}
      </a>
      {s.key === section.key && s.pages.map((p) => renderLink(p))}
      {s.key === section.key &&
        s.groups?.map((g) => (
          <div key={g.label}>
            <div className="mx-3 mb-1 mt-3.5 font-mono text-xs uppercase tracking-wider text-muted-foreground">
              {g.label}
            </div>
            {g.pages.map((p) => renderLink(p))}
          </div>
        ))}
    </div>
  );

  return (
    <div className="flex min-h-screen flex-col">
      <LayeredStrip onNavigate={navigate} />
      <div className="flex flex-1 items-stretch">
        <nav className="w-56 shrink-0 border-r border-border bg-sidebar px-2 py-4" aria-label="导航">
          {resolved.map((s) => renderSection(s))}
        </nav>
        <main className="min-w-0 flex-1 px-7 py-6">
          <page.component />
        </main>
      </div>
      <Toaster position="bottom-right" richColors closeButton />
    </div>
  );
}
