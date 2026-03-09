import { Info } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function AboutDialog() {
  const { t } = useTranslation();
  
  const { data: systemStatus } = useQuery({
    queryKey: ["system-status-about"],
    queryFn: () => api.getSystemStatus(),
    retry: false,
  });

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon">
          <Info className="size-[1.2rem]" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("dashboard.about.title")}</DialogTitle>
          <DialogDescription>
            {t("dashboard.about.subtitle")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <p className="text-sm font-medium">{t("dashboard.about.version")}</p>
            <p className="text-sm text-muted-foreground font-mono">
              {systemStatus?.version || "Loading..."}
            </p>
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium">{t("dashboard.about.website")}</p>
            <a 
              href="https://www.powermem.ai" 
              target="_blank" 
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline"
            >
              www.powermem.ai
            </a>
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium">{t("dashboard.about.copyright")}</p>
            <p className="text-sm text-muted-foreground">
              © {new Date().getFullYear()} PowerMem. {t("dashboard.about.allRightsReserved")}
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
