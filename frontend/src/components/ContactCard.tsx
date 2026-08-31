import type { Contact } from "../hooks/useSSE";

const STATUS_STYLE: Record<string, string> = {
  verified: "bg-green-100 text-green-800",
  probable: "bg-amber-100 text-amber-800",
  unknown: "bg-gray-100 text-gray-600",
};

// Reason identifiers come from the backend as fixed slugs so the UI owns the
// wording; anything unmapped falls back to the slug itself.
const REASON_LABEL: Record<string, string> = {
  matched_verified_domain_pattern: "company's verified email pattern",
  matched_probable_domain_pattern: "company's likely email pattern",
  pattern_failed_at_domain: "this pattern failed here before",
  accept_all_domain_penalty: "domain accepts any address",
  high_recruiter_title_relevance: "recruiter",
  medium_people_title_relevance: "people ops",
  low_hiring_manager_title_relevance: "hiring manager",
  source_company_domain: "found on company site",
  source_linkedin_profile: "found on LinkedIn",
  name_unparseable: "name may not split correctly",
};

const label = (r: string) => REASON_LABEL[r] ?? r;

// Eligibility is about the *address*, not the person: whether it can be
// mailed without spending a verification credit.
const ELIGIBILITY_LABEL: Record<string, string> = {
  verified_address: "verified",
  stored_verified_address: "verified",
  auto_promoted_pattern: "pattern match",
  needs_verification: "needs verifying",
  no_guess: "no address",
};

interface ContactCardProps {
  contact: Contact;
  onReachOut?: () => void;
}

export default function ContactCard({ contact, onReachOut }: ContactCardProps) {
  // A guess is never presented as a confirmed address — the top one is shown
  // as "likely", the rest stay collapsed behind the count.
  const guesses = contact.guesses ?? [];
  const top = guesses[0];

  return (
    <div className="border rounded p-3 text-xs bg-white w-56">
      <p className="font-semibold">{contact.name}</p>
      <p className="text-gray-500 mb-1">{contact.title}</p>
      {contact.email && (
        <a
          href={`mailto:${contact.email}`}
          className="text-blue-600 hover:underline block"
        >
          {contact.email}
        </a>
      )}
      {!contact.email && top && (
        <div className="mt-1">
          <p className="text-gray-700">
            {top.email}{" "}
            <span className={`px-1 rounded ${STATUS_STYLE[top.status] ?? STATUS_STYLE.unknown}`}>
              {top.status === "unknown" ? "guess" : top.status}
            </span>
          </p>
          {top.reasons.length > 0 && (
            <p className="text-gray-400 mt-0.5">{top.reasons.map(label).join(" · ")}</p>
          )}
          {guesses.length > 1 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-gray-500">
                {guesses.length - 1} other guesses
              </summary>
              <ul className="mt-1 space-y-0.5 text-gray-500">
                {guesses.slice(1).map((g) => (
                  <li key={g.email}>{g.email}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {contact.reasons && contact.reasons.length > 0 && (
        <p className="text-gray-400 mt-1">{contact.reasons.map(label).join(" · ")}</p>
      )}
      {contact.eligibility && (
        <p className="mt-1">
          <span
            className={`px-1 rounded ${
              contact.eligibility.eligible ? STATUS_STYLE.verified : STATUS_STYLE.probable
            }`}
          >
            {ELIGIBILITY_LABEL[contact.eligibility.basis] ?? contact.eligibility.basis}
          </span>
        </p>
      )}
      {onReachOut && (
        <button
          className="mt-2 w-full px-2 py-1 border border-gray-400 font-mono text-[10px] uppercase tracking-wide text-gray-600 hover:border-black hover:text-black"
          onClick={onReachOut}
        >
          Reach out
        </button>
      )}
      {(contact.linkedin || contact.source_url) && (
        <a
          href={contact.linkedin ?? contact.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-400 hover:underline block mt-1"
        >
          {contact.source_type === "company_site" ? "Company page →" : "LinkedIn →"}
        </a>
      )}
    </div>
  );
}
