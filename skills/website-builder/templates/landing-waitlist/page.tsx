"use client";

import { useState } from "react";

export default function Home() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("{{WAITLIST_API_URL}}", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });

      if (!res.ok) {
        throw new Error("Something went wrong. Please try again.");
      }

      setSubmitted(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Something went wrong."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-white">
      {/* Hero */}
      <section className="flex flex-col items-center justify-center px-6 py-24 sm:py-32 lg:py-40">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mb-6 inline-flex items-center rounded-full bg-gray-100 px-4 py-1.5 text-sm font-medium text-gray-700">
            Coming Soon
          </div>
          <h1 className="text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">
            {"{{HEADLINE}}"}
          </h1>
          <p className="mt-6 text-lg leading-8 text-gray-600">
            Be the first to know when {"{{PRODUCT_NAME}}"} launches.
            Join the waitlist and get early access.
          </p>

          {/* Form */}
          <div className="mt-10">
            {submitted ? (
              <div className="rounded-xl border border-green-200 bg-green-50 p-6">
                <p className="text-lg font-semibold text-green-800">
                  You&apos;re on the list!
                </p>
                <p className="mt-2 text-sm text-green-700">
                  We&apos;ll notify you as soon as {"{{PRODUCT_NAME}}"}{" "}
                  is ready.
                </p>
              </div>
            ) : (
              <form
                onSubmit={handleSubmit}
                className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center"
              >
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="w-full rounded-lg border border-gray-300 px-4 py-3 text-base text-gray-900 placeholder-gray-400 focus:border-black focus:outline-none focus:ring-1 focus:ring-black sm:w-72"
                />
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full rounded-lg bg-black px-8 py-3 text-base font-semibold text-white shadow-sm hover:bg-gray-800 disabled:opacity-50 transition-colors sm:w-auto"
                >
                  {loading ? "Joining..." : "{{CTA_TEXT}}"}
                </button>
              </form>
            )}
            {error && (
              <p className="mt-3 text-sm text-red-600">{error}</p>
            )}
          </div>

          {/* Subscriber count */}
          <p className="mt-6 text-sm text-gray-500">
            Join 1,200+ people already on the waitlist
          </p>
        </div>
      </section>

      {/* Value Props */}
      <section className="border-t border-gray-100 bg-gray-50 px-6 py-20">
        <div className="mx-auto max-w-4xl">
          <div className="grid gap-8 sm:grid-cols-3">
            <div className="text-center">
              <div className="text-3xl">{"\u26A1"}</div>
              <h3 className="mt-3 text-lg font-semibold text-gray-900">
                Fast Setup
              </h3>
              <p className="mt-2 text-base text-gray-600">
                Get started in under 5 minutes. No complex configuration.
              </p>
            </div>
            <div className="text-center">
              <div className="text-3xl">{"\uD83D\uDCC8"}</div>
              <h3 className="mt-3 text-lg font-semibold text-gray-900">
                Real Results
              </h3>
              <p className="mt-2 text-base text-gray-600">
                Built for outcomes, not just features. Measure what matters.
              </p>
            </div>
            <div className="text-center">
              <div className="text-3xl">{"\uD83D\uDD12"}</div>
              <h3 className="mt-3 text-lg font-semibold text-gray-900">
                Private & Secure
              </h3>
              <p className="mt-2 text-base text-gray-600">
                Your data stays yours. Enterprise-grade security from day one.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-200 py-8 text-center text-sm text-gray-500">
        <p>
          &copy; {new Date().getFullYear()} {"{{PRODUCT_NAME}}"}. All rights
          reserved.
        </p>
      </footer>
    </main>
  );
}
