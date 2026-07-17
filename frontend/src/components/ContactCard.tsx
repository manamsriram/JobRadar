import type { Contact } from "../hooks/useSSE";

export default function ContactCard({ contact }: { contact: Contact }) {
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
      {contact.linkedin && (
        <a
          href={contact.linkedin}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-400 hover:underline block mt-1"
        >
          LinkedIn →
        </a>
      )}
    </div>
  );
}
