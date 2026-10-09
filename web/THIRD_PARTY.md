# Browser dependency notices

Production builds generate `assets/third-party-licenses.md` with Vite's bundled dependency
licenses. The generated file ships with the browser UI in both Python distributions.
Python dependencies are installed separately and retain their own distribution notices.

Supplemental notices copied into the UI:

- `public/assets/shadcn-license.txt`: the MIT notice for the copied shadcn components;
  see `src/components/ui/README.md` for provenance.
- `public/assets/tailwindcss-license.txt`: the MIT notice from Tailwind CSS 4.3.3,
  whose styles are included in the generated CSS.
- `public/assets/react-remove-scroll-bar-license.txt`: the MIT notice omitted from the
  package's 2.3.8 npm archive, copied from [upstream LICENSE](https://github.com/theKashey/react-remove-scroll-bar/blob/7301c160fda44cb8cf2b9fdfde61efad35736196/LICENSE).

When upgrading these dependencies, review the supplemental notices as well as the generated
license file. Packaging rejects missing notice files; installed-package checks verify they
are served from the bundled UI.

The Pi mark comes from the official Pi press assets; see `public/assets/pi-logo-notice.txt`.
