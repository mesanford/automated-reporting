"use client";

import React, { createContext, useContext, useEffect, useState } from 'react';
import {
  GoogleAuthProvider,
  onIdTokenChanged,
  signInWithPopup,
  signOut as fbSignOut,
  type User,
} from 'firebase/auth';
import { firebaseConfigured, getFirebaseAuth } from './firebase';

interface AuthState {
  user: User | null;
  loading: boolean;
  configured: boolean;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  getIdToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const auth = getFirebaseAuth();
    if (!auth) {
      setTimeout(() => {
        setLoading(false);
      }, 0);
      return;
    }
    const unsub = onIdTokenChanged(auth, (u) => {
      setUser(u);
      setLoading(false);
    });
    return () => unsub();
  }, []);

  const value: AuthState = {
    user,
    loading,
    configured: firebaseConfigured,
    signIn: async () => {
      const auth = getFirebaseAuth();
      if (!auth) throw new Error('Firebase is not configured.');
      const provider = new GoogleAuthProvider();
      await signInWithPopup(auth, provider);
    },
    signOut: async () => {
      const auth = getFirebaseAuth();
      if (!auth) return;
      await fbSignOut(auth);
    },
    getIdToken: async () => {
      const auth = getFirebaseAuth();
      if (!auth || !auth.currentUser) return null;
      return auth.currentUser.getIdToken();
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}

/** Read the current ID token without subscribing — for the api/sse helpers. */
export async function getCurrentIdToken(): Promise<string | null> {
  const auth = getFirebaseAuth();
  if (!auth || !auth.currentUser) return null;
  return auth.currentUser.getIdToken();
}
