import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { RefreshCcw, Search, User, Users } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export const Route = createFileRoute("/users")({
  component: UsersPage,
});

function UsersPage() {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState("");

  const {
    data: users,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.getUsers(),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <RefreshCcw className="h-12 w-12 animate-spin text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              Error loading users
            </CardTitle>
            <CardDescription className="text-destructive/80">
              {(error as Error).message}
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const filteredUsers = (users || []).filter((user) =>
    user.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  return (
    <div className="p-4 space-y-6 w-full lg:w-7xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
          <Users className="text-primary" />
          User Management
        </h1>
        <p className="text-muted-foreground text-sm">
          Browse and analyze cognitive profiles of unique users in the system.
        </p>
      </div>

      <div className="relative max-w-md">
        <Search
          className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
          size={18}
        />
        <Input
          placeholder="Search by User ID..."
          className="pl-10"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {filteredUsers.length > 0 ? (
          filteredUsers.map((userId) => (
            <Card
              key={userId}
              className="hover:bg-accent/50 transition-colors cursor-pointer group border-border/50 flex flex-col"
              onClick={() =>
                navigate({
                  to: "/",
                  search: { user_id: userId, agent_id: undefined },
                })
              }
            >
              <CardHeader>
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center border border-primary/20 shrink-0">
                    <User size={20} />
                  </div>
                  <div className="min-w-0">
                    <CardTitle className="text-base font-bold group-hover:text-primary transition-colors truncate">
                      {userId}
                    </CardTitle>
                  </div>
                </div>
              </CardHeader>
            </Card>
          ))
        ) : (
          <Card className="w-full border-dashed bg-muted/20">
            <CardContent className="p-12 text-center text-muted-foreground italic">
              No users found matching "{searchTerm}"
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
