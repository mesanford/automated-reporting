const version = process.versions.node;
const [majorRaw, minorRaw] = version.split('.');
const major = Number(majorRaw);
const minor = Number(minorRaw);

const isSupported =
  (major === 20 && minor >= 9) ||
  major === 22 ||
  major === 24 ||
  major === 25;

if (!isSupported) {
  console.error(
    [
      `Unsupported Node.js version ${version}.`,
      'This frontend supports Node 20.9+, 22.x, 24.x, or 25.x.',
      'If Node 25 hangs, switch to Node 22 LTS, reinstall dependencies in frontend/, then run npm run dev again.',
    ].join(' '),
  );
  process.exit(1);
}