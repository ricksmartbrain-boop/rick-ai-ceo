"use client";

import { useState } from "react";

const features = [
  {
    icon: "\u26A1",
    title: "Lightning Fast",
    description:
      "Get results in seconds, not hours. Built for speed from the ground up.",
  },
  {
    icon: "\uD83D\uDD12",
    title: "Secure by Default",
    description:
      "Enterprise-grade security with end-to-end encryption and SOC 2 compliance.",
  },
  {
    icon: "\uD83D\uDCC8",
    title: "Analytics Built In",
    description:
      "Track every metric that matters with real-time dashboards and reports.",
  },
  {
    icon: "\uD83E\uDD1D",
    title: "Integrations",
    description:
      "Connect with the tools you already use. 50+ integrations out of the box.",
  },
];

const testimonials = [
  {
    quote:
      "This completely transformed how we work. We saved 20 hours per week.",
    author: "Sarah K.",
    role: "VP of Sales",
  },
  {
    quote:
      "The ROI was immediate. We saw results within the first week.",
    author: "Marcus J.",
    role: "Founder & CEO",
  },
  {
    quote:
      "Best investment we made this year. The team loves it.",
    author: "Elena R.",
    role: "Head of Operations",
  },
];

const faqs = [
  {
    question: "How does it work?",
    answer:
      "Sign up, connect your tools, and start seeing results within minutes. Our onboarding wizard guides you through everything.",
  },
  {
    question: "Is there a free trial?",
    answer:
      "Yes! You get 14 days of full access, no credit card required. Cancel anytime.",
  },
  {
    question: "What kind of support do you offer?",
    answer:
      "We offer email support for all plans and priority Slack support for Pro and Enterprise customers.",
  },
  {
    question: "Can I cancel anytime?",
    answer:
      "Absolutely. No contracts, no cancellation fees. You can cancel your subscription at any time.",
  },
];

function FAQItem({
  question,
  answer,
}: {
  question: string;
  answer: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-gray-200">
      <button
        className="flex w-full items-center justify-between py-5 text-left"
        onClick={() => setOpen(!open)}
      >
        <span className="text-base font-medium text-gray-900">{question}</span>
        <span className="ml-4 text-gray-500">{open ? "\u2212" : "+"}</span>
      </button>
      {open && (
        <p className="pb-5 text-base text-gray-600">{answer}</p>
      )}
    </div>
  );
}

export default function Home() {
  return (
    <main className="min-h-screen bg-white">
      {/* Hero */}
      <section className="px-6 py-24 sm:py-32">
        <div className="mx-auto max-w-3xl text-center">
          <h1 className="text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">
            {"{{HEADLINE}}"}
          </h1>
          <p className="mt-6 text-xl leading-8 text-gray-600">
            {"{{PRODUCT_NAME}}"} gives you the tools to scale faster,
            close more deals, and spend less time on busywork.
          </p>
          <div className="mt-10">
            <a
              href={"{{STRIPE_CHECKOUT_URL}}"}
              className="rounded-lg bg-black px-8 py-4 text-base font-semibold text-white shadow-sm hover:bg-gray-800 transition-colors"
            >
              {"{{CTA_TEXT}}"}
            </a>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="bg-gray-50 px-6 py-20">
        <div className="mx-auto max-w-5xl">
          <h2 className="text-center text-3xl font-bold text-gray-900">
            Everything you need
          </h2>
          <p className="mt-4 text-center text-lg text-gray-600">
            Powerful features, simple interface.
          </p>
          <div className="mt-12 grid gap-8 sm:grid-cols-2">
            {features.map((feature) => (
              <div
                key={feature.title}
                className="rounded-xl bg-white p-8 shadow-sm"
              >
                <span className="text-3xl">{feature.icon}</span>
                <h3 className="mt-4 text-lg font-semibold text-gray-900">
                  {feature.title}
                </h3>
                <p className="mt-2 text-base text-gray-600">
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Testimonials */}
      <section className="px-6 py-20">
        <div className="mx-auto max-w-5xl">
          <h2 className="text-center text-3xl font-bold text-gray-900">
            Trusted by fast-growing teams
          </h2>
          <div className="mt-12 grid gap-8 sm:grid-cols-3">
            {testimonials.map((t) => (
              <div
                key={t.author}
                className="rounded-xl border border-gray-200 p-6"
              >
                <p className="text-base text-gray-700">
                  &ldquo;{t.quote}&rdquo;
                </p>
                <div className="mt-4">
                  <p className="text-sm font-semibold text-gray-900">
                    {t.author}
                  </p>
                  <p className="text-sm text-gray-500">{t.role}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section className="bg-gray-50 px-6 py-20">
        <div className="mx-auto max-w-md text-center">
          <h2 className="text-3xl font-bold text-gray-900">
            Simple pricing
          </h2>
          <p className="mt-4 text-lg text-gray-600">
            One plan, everything included. No hidden fees.
          </p>
          <div className="mt-10 rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
            <p className="text-5xl font-bold text-gray-900">$99</p>
            <p className="mt-2 text-base text-gray-500">per month</p>
            <ul className="mt-8 space-y-3 text-left text-base text-gray-700">
              <li className="flex items-center gap-2">
                <span className="text-green-600">&#10003;</span> Unlimited access
              </li>
              <li className="flex items-center gap-2">
                <span className="text-green-600">&#10003;</span> All integrations
              </li>
              <li className="flex items-center gap-2">
                <span className="text-green-600">&#10003;</span> Priority support
              </li>
              <li className="flex items-center gap-2">
                <span className="text-green-600">&#10003;</span> 14-day free trial
              </li>
            </ul>
            <div className="mt-8">
              <a
                href={"{{STRIPE_CHECKOUT_URL}}"}
                className="block w-full rounded-lg bg-black px-6 py-3 text-center text-base font-semibold text-white hover:bg-gray-800 transition-colors"
              >
                {"{{CTA_TEXT}}"}
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="px-6 py-20">
        <div className="mx-auto max-w-2xl">
          <h2 className="text-center text-3xl font-bold text-gray-900">
            Frequently asked questions
          </h2>
          <div className="mt-12">
            {faqs.map((faq) => (
              <FAQItem
                key={faq.question}
                question={faq.question}
                answer={faq.answer}
              />
            ))}
          </div>
        </div>
      </section>

      {/* Footer CTA */}
      <section className="bg-black px-6 py-20">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-bold text-white">
            Ready to get started?
          </h2>
          <p className="mt-4 text-lg text-gray-300">
            Join thousands of teams already using {"{{PRODUCT_NAME}}"}.
          </p>
          <div className="mt-8">
            <a
              href={"{{STRIPE_CHECKOUT_URL}}"}
              className="rounded-lg bg-white px-8 py-4 text-base font-semibold text-black hover:bg-gray-100 transition-colors"
            >
              {"{{CTA_TEXT}}"}
            </a>
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
