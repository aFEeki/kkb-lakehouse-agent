// The backend composes one answer string with real structure in it: findings as plain
// lines, caveats indented with "-", disclosures under a header and prefixed "!", and the
// plan on a final line. Printing it as one block throws that structure away, and the
// trust layer is the part this project is judged hardest on.

type Section = {
  findings: string[];
  caveats: string[];
  disclosures: string[];
  plan: string | null;
};

const DISCLOSURE_HEADER = "Açıklanması gerekenler:";

export function parseAnswer(answer: string): Section {
  const findings: string[] = [];
  const caveats: string[] = [];
  const disclosures: string[] = [];
  let plan: string | null = null;

  for (const raw of answer.split("\n")) {
    const line = raw.trim();
    if (!line || line === DISCLOSURE_HEADER) continue;
    if (line.startsWith("Plan:")) {
      plan = line.slice("Plan:".length).trim();
    } else if (line.startsWith("!")) {
      disclosures.push(line.replace(/^!\s*/, ""));
    } else if (line.startsWith("-")) {
      caveats.push(line.replace(/^-\s*/, ""));
    } else {
      findings.push(line);
    }
  }
  return { findings, caveats, disclosures, plan };
}

export function Answer({ answer }: { answer: string }) {
  const { findings, caveats, disclosures, plan } = parseAnswer(answer);

  return (
    <div className="mt-6 space-y-5">
      {findings.map((finding, index) => (
        <p
          className={
            index === 0
              ? "text-[15px] leading-relaxed text-slate-100"
              : "leading-relaxed text-slate-300"
          }
          key={finding}
        >
          {finding}
        </p>
      ))}

      {caveats.length > 0 ? (
        <ul className="space-y-1.5 border-l-2 border-slate-700 pl-4">
          {caveats.map((caveat) => (
            <li className="text-sm leading-relaxed text-slate-400" key={caveat}>
              {caveat}
            </li>
          ))}
        </ul>
      ) : null}

      {disclosures.length > 0 ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-amber-300/80">
            Açıklanması gerekenler
          </p>
          <ul className="mt-2 space-y-1">
            {disclosures.map((item) => (
              <li className="text-sm leading-relaxed text-amber-100/90" key={item}>
                {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {plan ? (
        // The plan is the audit trail, including rejected attempts, so it stays reachable
        // and out of the way: a reader checking the work opens it, a reader reading the
        // finding is not made to scroll past it.
        <details className="group">
          <summary className="cursor-pointer list-none text-xs text-slate-500 hover:text-slate-400">
            <span className="text-slate-600 transition group-open:rotate-90 inline-block">▸</span>{" "}
            Yürütme planı
          </summary>
          <p className="mt-2 font-mono text-xs leading-relaxed text-slate-500">{plan}</p>
        </details>
      ) : null}
    </div>
  );
}
