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

  const actAs = (id: string) => { localStorage.setItem("devUserId", id); setActingId(id); };
  return <IdentityCtx.Provider value={{ me, users, actAs }}>{children}</IdentityCtx.Provider>;
}
export const useIdentity = () => useContext(IdentityCtx);
