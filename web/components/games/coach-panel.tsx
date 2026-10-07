import { formatRecord } from "@/lib/core/game-lines";
import { ordinal } from "@/lib/core/team-strength";
import type { CoachSummary, WinLoss } from "@/lib/core/coach";

/**
 * Both head coaches (client, 2026-10-06): who they are, how long they have
 * been at the school, and their records BEFORE this season — at the school
 * and over the career, with conference and bowl records where CFBD recorded
 * them. This season to date is in the records panel above, from our own
 * games; see `lib/core/coach.ts` for why the two are kept apart.
 */
export function CoachPanel({
  season,
  away,
  home,
}: {
  season: number;
  away: { school: string; coach: CoachSummary | null };
  home: { school: string; coach: CoachSummary | null };
}) {
  if (!away.coach && !home.coach) return null;
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">🧢 Head coaches</h2>
        <p className="text-muted text-xs">
          Records before {season}, from CollegeFootballData. This season so far
          is in the records above.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {[away, home].map((side) => (
          <CoachCard key={side.school} school={side.school} coach={side.coach} />
        ))}
      </div>
    </section>
  );
}

const MONTH = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

function CoachCard({ school, coach }: { school: string; coach: CoachSummary | null }) {
  if (!coach) {
    return (
      <div className="bg-panel-inset flex flex-col gap-1 rounded-xl p-3">
        <span className="label-caption">{school}</span>
        <span className="text-dim text-xs">No head coach on record.</span>
      </div>
    );
  }
  const record = (wl: WinLoss | null) => (wl ? formatRecord(wl.w, wl.l) : "—");
  return (
    <div className="bg-panel-inset flex flex-col gap-2 rounded-xl p-3">
      <div className="flex flex-col">
        <span className="label-caption">{school}</span>
        <span className="text-ink text-sm font-extrabold">{coach.name}</span>
        <span className="text-dim text-[0.6875rem]">
          {coach.seasonAtSchool === 1
            ? "First season"
            : `${ordinal(coach.seasonAtSchool)} season`}
          {coach.hireDate ? ` · hired ${MONTH.format(new Date(coach.hireDate))}` : ""}
        </span>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {coach.atSchool.seasons > 0 ? (
          <>
            <Stat label={`At ${school}`} value={record(coach.atSchool.record)} />
            {coach.atSchool.conference ? (
              <Stat label="Conf" value={record(coach.atSchool.conference)} />
            ) : null}
            {coach.atSchool.postseason &&
            coach.atSchool.postseason.w + coach.atSchool.postseason.l > 0 ? (
              <Stat label="Bowls" value={record(coach.atSchool.postseason)} />
            ) : null}
          </>
        ) : (
          <span className="text-dim self-end text-xs">No completed season at {school}.</span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        <Stat
          label="Career"
          value={coach.career.seasons > 0 ? record(coach.career.record) : "—"}
        />
        <Stat label="Seasons" value={String(coach.career.seasons)} />
        {coach.career.schools > 1 ? (
          <Stat label="Schools" value={String(coach.career.schools)} />
        ) : null}
        {coach.career.postseason &&
        coach.career.postseason.w + coach.career.postseason.l > 0 ? (
          <Stat label="Bowls" value={record(coach.career.postseason)} />
        ) : null}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex flex-col">
      <span className="label-caption">{label}</span>
      <span className="text-ink text-sm font-extrabold tabular-nums">{value}</span>
    </span>
  );
}
