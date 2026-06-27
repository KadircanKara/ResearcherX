"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { apiGet, setDevUserId } from "./api";
import type { User } from "./types";

type Ctx = { me: User | null; users: User[]; actAs: (id: string) => void };
const IdentityCtx = createContext<Ctx>({ me: null, users: [], actAs: () => {} });

export function IdentityProvider({ children }: { children: React.ReactNode }) {
  const [users, setUsers] = useState<User[]>([]);
  const [me, setMe] = useState<User | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  useEffect(() => { setActingId(localStorage.getItem("devUserId")); }, []);
  useEffect(() => {
    setDevUserId(actingId);
    apiGet<User[]>("/users").then(setUsers).catch(() => {});
    apiGet<User>("/me").then(setMe).catch(() => {});
  }, [actingId]);

  // Auto-default to "You" (first seed user) when users load but no identity
  // is explicitly selected. Persists to localStorage so subsequent page loads
  // start with a valid identity and don't hit 401 on project-scoped requests.
  useEffect(() => {
    if (actingId === null && users.length > 0) {
      const defaultId = users[0].id;
      localStorage.setItem("devUserId", defaultId);
      setActingId(defaultId);
    }
  }, [actingId, users]);

  const actAs = (id: string) => { localStorage.setItem("devUserId", id); setActingId(id); };
  return <IdentityCtx.Provider value={{ me, users, actAs }}>{children}</IdentityCtx.Provider>;
}
export const useIdentity = () => useContext(IdentityCtx);
