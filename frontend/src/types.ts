export interface Contact {
  name: string;
  title: string | null;
  email: string | null;
  linkedin: string | null;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  ats: string;
  posted_at: string;
  applied?: boolean;
  contacts?: Contact[];
  description?: string;
}
