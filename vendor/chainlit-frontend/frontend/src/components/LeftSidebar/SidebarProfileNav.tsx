import capitalize from 'lodash/capitalize';
import { CircleHelp, LogOut, Settings } from 'lucide-react';

import { useAuth } from '@chainlit/react-client';

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { useSidebar } from '@/components/ui/sidebar';

export default function SidebarProfileNav() {
  const { user, logout } = useAuth();
  const { isMobile, open, openMobile } = useSidebar();

  if (!user) return null;

  const sidebarOpen = isMobile ? openMobile : open;
  const firstName = String(user.metadata?.first_name || '').trim();
  const metadataDisplayName = String(user.metadata?.display_name || '').trim();
  const topLevelDisplayName = String(user.display_name || '').trim();
  const firstFromDisplay =
    metadataDisplayName.split(/\s+/)[0] ||
    topLevelDisplayName.split(/\s+/)[0] ||
    '';
  const displayName =
    firstName ||
    firstFromDisplay ||
    'User';
  const initial = capitalize(displayName[0] || 'U');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          id="sidebar-user-nav-button"
          variant="ghost"
          className={
            sidebarOpen
              ? 'h-11 w-full justify-start gap-2 rounded-lg px-2'
              : 'h-9 w-9 rounded-full p-0'
          }
        >
          <Avatar className="h-8 w-8">
            <AvatarImage src={user?.metadata?.image} alt="user image" />
            <AvatarFallback className="bg-primary text-primary-foreground font-semibold">
              {initial}
            </AvatarFallback>
          </Avatar>
          {sidebarOpen ? (
            <div className="min-w-0 text-left">
              <div className="truncate text-sm font-medium">{displayName}</div>
            </div>
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56" side="top" align="start" forceMount>
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col space-y-1">
            <p className="text-sm font-medium leading-none">{displayName}</p>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem>
          <Settings className="mr-2 h-4 w-4" />
          Settings
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => window.open('https://docs.chainlit.io', '_blank')}
        >
          <CircleHelp className="mr-2 h-4 w-4" />
          Help
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => logout(true)}>
          <LogOut className="mr-2 h-4 w-4" />
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
