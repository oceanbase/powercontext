import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Eye, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import type { UserProfile } from "../lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const Route = createFileRoute("/user-profile")({
  component: UserProfilePage,
});

function UserProfilePage() {
  const { t } = useTranslation();
  const [userIdInput, setUserIdInput] = useState("");
  const [userIdFilter, setUserIdFilter] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedProfile, setSelectedProfile] = useState<UserProfile | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const pageSize = 20;

  const { data, isLoading, error } = useQuery({
    queryKey: ["user-profiles", userIdFilter, currentPage],
    queryFn: () => 
      api.getAllUserProfiles(
        userIdFilter || undefined,
        pageSize,
        (currentPage - 1) * pageSize,
        Boolean(userIdFilter)
      ),
  });

  const profiles = data?.profiles || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / pageSize);

  const handleFilter = async () => {
    setIsSearching(true);
    try {
      const trimmedInput = userIdInput.trim();
      setUserIdFilter(trimmedInput);
      setCurrentPage(1);
      
      // Give a brief moment for the UI to update
      await new Promise(resolve => setTimeout(resolve, 300));
      
      if (trimmedInput) {
        toast.success(t("userProfile.filterApplied"));
      } else {
        toast.success(t("userProfile.filterCleared"));
      }
    } catch (error) {
      toast.error(t("common.error"), {
        description: t("common.tryAgain"),
      });
    } finally {
      setIsSearching(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleFilter();
    }
  };

  const renderTruncatedWithTooltip = (
    value: string | undefined,
    fallback: string,
    maxWidthClass: string,
  ) => {
    const displayValue = value || fallback;
    const textNode = (
      <span className={`block ${maxWidthClass} truncate`}>{displayValue}</span>
    );

    if (!value) {
      return textNode;
    }

    return (
      <Tooltip>
        <TooltipTrigger asChild>{textNode}</TooltipTrigger>
        <TooltipContent
          side="top"
          align="start"
          className="max-w-[460px] break-all text-xs"
        >
          {value}
        </TooltipContent>
      </Tooltip>
    );
  };

  const formatTopics = (topics: Record<string, any> | undefined) => {
    if (!topics) return t("userProfile.detail.none");
    const topicKeys = Object.keys(topics);
    if (topicKeys.length === 0) return t("userProfile.detail.none");
    return topicKeys.slice(0, 3).join(", ") + (topicKeys.length > 3 ? "..." : "");
  };

  const formatDate = (dateString: string) => {
    try {
      return new Date(dateString).toLocaleString();
    } catch {
      return dateString;
    }
  };

  if (error) {
    return (
      <div className="flex h-[50vh] items-center justify-center">
        <p className="text-destructive">Error: {(error as Error).message}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-4 p-4 md:gap-6 md:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t("userProfile.title")}</h1>
          <p className="text-muted-foreground">{t("userProfile.description")}</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <Input
                placeholder={t("userProfile.filterByUserId")}
                value={userIdInput}
                onChange={(e) => setUserIdInput(e.target.value)}
                onKeyPress={handleKeyPress}
              />
            </div>
            <Button onClick={handleFilter} disabled={isSearching} className="gap-2">
              <Search className={`size-4 ${isSearching ? "animate-pulse" : ""}`} />
              {isSearching ? t("userProfile.searching") : t("userProfile.search")}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <p className="text-muted-foreground">{t("userProfile.loading")}</p>
            </div>
          ) : profiles.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <p className="text-muted-foreground">
                {t("userProfile.noProfiles")}
              </p>
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("userProfile.columns.userId")}</TableHead>
                    <TableHead>{t("userProfile.columns.profileContent")}</TableHead>
                    <TableHead>{t("userProfile.columns.topics")}</TableHead>
                    <TableHead>{t("userProfile.columns.updatedAt")}</TableHead>
                    <TableHead className="text-right">{t("userProfile.columns.actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {profiles.map((profile) => (
                    <TableRow key={profile.id}>
                      <TableCell className="font-medium">
                        {renderTruncatedWithTooltip(
                          profile.user_id,
                          t("userProfile.detail.none"),
                          "max-w-[220px]",
                        )}
                      </TableCell>
                      <TableCell className="max-w-md">
                        {renderTruncatedWithTooltip(
                          profile.profile_content,
                          t("userProfile.detail.none"),
                          "max-w-[420px]",
                        )}
                      </TableCell>
                      <TableCell>{formatTopics(profile.topics)}</TableCell>
                      <TableCell>{formatDate(profile.updated_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setSelectedProfile(profile)}
                        >
                          <Eye className="size-4 mr-1" />
                          {t("userProfile.viewDetails")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {totalPages > 1 && (
                <div className="mt-4 flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    {t("userProfile.showing", {
                      count: profiles.length,
                      total: total,
                    })}
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                      disabled={currentPage === 1}
                    >
                      <ChevronLeft className="size-4 mr-1" />
                      {t("userProfile.prev")}
                    </Button>
                    <span className="text-sm">
                      {t("userProfile.page", { page: currentPage, total: totalPages })}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                      disabled={currentPage === totalPages}
                    >
                      {t("userProfile.next")}
                      <ChevronRight className="size-4 ml-1" />
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Sheet open={!!selectedProfile} onOpenChange={(open) => !open && setSelectedProfile(null)}>
        <SheetContent className="sm:max-w-xl overflow-y-auto p-6">
          <SheetHeader className="space-y-2">
            <SheetTitle>{t("userProfile.detail.title")}</SheetTitle>
          </SheetHeader>
          {selectedProfile && (
            <div className="mt-6 space-y-6 px-1">
              <div className="space-y-2">
                <h4 className="text-sm font-semibold">{t("userProfile.detail.userId")}</h4>
                <p className="text-sm text-muted-foreground whitespace-pre-wrap break-all">
                  {selectedProfile.user_id || t("userProfile.detail.none")}
                </p>
              </div>

              <div className="space-y-2">
                <h4 className="text-sm font-semibold">{t("userProfile.detail.content")}</h4>
                <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                  {selectedProfile.profile_content || t("userProfile.detail.none")}
                </p>
              </div>

              {selectedProfile.topics && Object.keys(selectedProfile.topics).length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">{t("userProfile.detail.topics")}</h4>
                  <pre className="text-sm text-muted-foreground bg-muted p-3 rounded-md overflow-auto">
                    {JSON.stringify(selectedProfile.topics, null, 2)}
                  </pre>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4 pt-4 border-t">
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">
                    {t("userProfile.detail.createdAt")}
                  </p>
                  <p className="text-sm">{formatDate(selectedProfile.created_at)}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">
                    {t("userProfile.detail.updatedAt")}
                  </p>
                  <p className="text-sm">{formatDate(selectedProfile.updated_at)}</p>
                </div>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
