import { createFileRoute } from "@tanstack/react-router";
import { Key, Save, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { getApiKey, setApiKey } from "../lib/api";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const [apiKey, setApiKeyInput] = useState(getApiKey());

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setApiKey(apiKey.trim());
      toast.success("Settings updated", {
        description: "API Key has been saved to local storage.",
      });
    } catch (err) {
      toast.error("Save failed", {
        description: "Could not save the API key to your browser.",
      });
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-2xl mx-auto">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Key className="text-primary" />
          Settings
        </h1>
        <p className="text-muted-foreground">
          Manage your dashboard configuration and authentication keys.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <CardDescription>
            Configure how the dashboard connects to the PowerMem server.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-start gap-4 p-4 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-600 dark:text-blue-400">
            <ShieldCheck className="mt-0.5 shrink-0" size={20} />
            <div className="text-sm">
              <p className="font-bold">Privacy & Security</p>
              <p className="opacity-90 leading-relaxed">
                Your API key is stored only in your browser's Local Storage. It
                is never transmitted to any third-party analytics or external
                servers.
              </p>
            </div>
          </div>

          <form onSubmit={handleSave} className="space-y-4">
            <div className="grid w-full items-center gap-2">
              <Label htmlFor="api-key">Server API Key</Label>
              <Input
                id="api-key"
                type="password"
                placeholder="Enter your PowerMem API Key..."
                value={apiKey}
                onChange={(e) => setApiKeyInput(e.target.value)}
                className="font-mono"
              />
              <p className="text-[0.8rem] text-muted-foreground">
                Required if `auth_enabled` is set to true on the server.
              </p>
            </div>

            <Button type="submit" className="w-full sm:w-auto gap-2">
              <Save size={16} />
              Save Changes
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
