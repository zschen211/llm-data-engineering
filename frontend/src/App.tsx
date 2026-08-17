import { useCallback, useEffect, useState } from "react";
import { LayeredStrip } from "./LayeredStrip";
import { assetPages } from "./pages/assets";
import { factoryPages } from "./pages/factory";
import "./styles.css";

interface PageDef {
  key: string;
  num: string;
  label: string;
  component: () => React.JSX.Element;
}

export default function App() {
  const [hash, setHash] = useState(() => window.location.hash || "#/info");

  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/info");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: string) => {
    window.location.hash = next;
  }, []);

  const assetGroup: PageDef[] = assetPages.map((p, i) => ({
    ...p,
    num: `${i + 1}`.padStart(2, "0"),
  }));
  const factoryGroup: PageDef[] = factoryPages.map((p, i) => ({
    ...p,
    num: `${i + 1}`.padStart(2, "0"),
  }));

  const page =
    assetGroup.find((p) => p.key === hash.slice(2)) ??
    factoryGroup.find((p) => p.key === hash.slice(2)) ??
    assetGroup[0];

  const renderSideGroup = (title: string, pages: PageDef[]) => (
    <div>
      <div className="side-group">{title}</div>
      {pages.map((p) => (
        <a
          key={p.key}
          className={`side-link ${p.key === page.key ? "active" : ""}`}
          href={`#/${p.key}`}
        >
          <span className="side-num">{p.num}</span>
          {p.label}
        </a>
      ))}
    </div>
  );

  return (
    <div className="app">
      <LayeredStrip onNavigate={navigate} />
      <div className="main">
        <nav className="sidebar" aria-label="导航">
          {renderSideGroup("资产层", assetGroup)}
          {renderSideGroup("数据工厂", factoryGroup)}
        </nav>
        <main className="content">
          <page.component />
        </main>
      </div>
    </div>
  );
}
