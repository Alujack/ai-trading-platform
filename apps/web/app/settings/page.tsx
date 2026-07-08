"use client";

import { SettingsPanels } from "../components/settings/SettingsPanels";

export default function SettingsPage() {
  return (
    <>
      <div className="w-full">
        <h1 className="text-lg font-semibold text-neutral-100">Settings</h1>
        <p className="mt-0.5 text-xs text-neutral-500">
          AI providers, Telegram alerts, and your broker account.
        </p>
      </div>
      <SettingsPanels />
    </>
  );
}
