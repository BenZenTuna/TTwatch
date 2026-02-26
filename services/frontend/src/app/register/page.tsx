"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { register } from "@/lib/api-client";
import { Shield } from "lucide-react";

const PASSWORD_RULES = [
  { test: (p: string) => p.length >= 10, label: "At least 10 characters" },
  { test: (p: string) => /[A-Z]/.test(p), label: "One uppercase letter" },
  { test: (p: string) => /[a-z]/.test(p), label: "One lowercase letter" },
  { test: (p: string) => /\d/.test(p), label: "One digit" },
];

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const passwordValid = PASSWORD_RULES.every((r) => r.test(password));

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");

    if (!passwordValid) {
      setError("Please meet all password requirements.");
      return;
    }

    setLoading(true);
    try {
      await register(email, displayName, password);
      router.push("/dashboard");
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Registration failed. Please try again.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="card w-full max-w-md p-8">
        <div className="flex items-center justify-center gap-3 mb-8">
          <Shield className="w-8 h-8 text-accent" />
          <h1 className="text-2xl font-bold text-gray-100">TTwatch</h1>
        </div>

        <h2 className="text-lg font-semibold text-gray-200 mb-6 text-center">
          Create your account
        </h2>

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-3 rounded-md mb-4 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-gray-400 mb-1"
            >
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input-field"
              placeholder="you@example.com"
              required
              autoFocus
            />
          </div>

          <div>
            <label
              htmlFor="displayName"
              className="block text-sm font-medium text-gray-400 mb-1"
            >
              Display Name
            </label>
            <input
              id="displayName"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field"
              placeholder="Your name"
              required
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-gray-400 mb-1"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field"
              placeholder="Create a strong password"
              required
            />
            {password.length > 0 && (
              <ul className="mt-2 space-y-1">
                {PASSWORD_RULES.map((rule) => (
                  <li
                    key={rule.label}
                    className={`text-xs flex items-center gap-1.5 ${
                      rule.test(password) ? "text-emerald-400" : "text-gray-500"
                    }`}
                  >
                    <span>{rule.test(password) ? "\u2713" : "\u2022"}</span>
                    {rule.label}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <button
            type="submit"
            disabled={loading || !passwordValid}
            className="btn-primary w-full"
          >
            {loading ? "Creating account..." : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-500">
          Already have an account?{" "}
          <Link href="/login" className="text-accent hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
