export default function Home() {
  return (
    <main className="min-h-screen bg-white">
      {/* Hero Section */}
      <section className="flex flex-col items-center justify-center px-6 py-24 sm:py-32 lg:py-40">
        <div className="mx-auto max-w-2xl text-center">
          <h1 className="text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">
            {"{{HEADLINE}}"}
          </h1>
          <p className="mt-6 text-lg leading-8 text-gray-600">
            {"{{PRODUCT_NAME}}"} helps you work smarter, not harder.
            Get started in minutes.
          </p>
          <div className="mt-10 flex items-center justify-center">
            <a
              href={"{{STRIPE_CHECKOUT_URL}}"}
              className="rounded-lg bg-black px-8 py-4 text-base font-semibold text-white shadow-sm hover:bg-gray-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-black transition-colors"
            >
              {"{{CTA_TEXT}}"}
            </a>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-200 py-8 text-center text-sm text-gray-500">
        <p>&copy; {new Date().getFullYear()} {"{{PRODUCT_NAME}}"}. All rights reserved.</p>
      </footer>
    </main>
  );
}
