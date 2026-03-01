import { useNavigate } from 'react-router-dom';

import SidebarTrigger from '@/components/header/SidebarTrigger';
import {
  Sidebar,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarSeparator,
  useSidebar
} from '@/components/ui/sidebar';

import NewChatButton from '../header/NewChat';
import SidebarProfileNav from './SidebarProfileNav';
import SearchChats from './Search';
import { ThreadHistory } from './ThreadHistory';

export default function LeftSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { isMobile, open, openMobile } = useSidebar();
  const navigate = useNavigate();
  const sidebarOpen = isMobile ? openMobile : open;

  return (
    <Sidebar collapsible="icon" {...props} className="border-none">
      <SidebarHeader className="py-3">
        {sidebarOpen ? (
          <div className="flex items-center justify-between">
            <SidebarTrigger />
            <div className="flex items-center">
              <SearchChats />
              <NewChatButton navigate={navigate} />
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1">
            <SidebarTrigger />
            <SearchChats />
            <NewChatButton navigate={navigate} />
          </div>
        )}
      </SidebarHeader>
      <ThreadHistory />
      <SidebarFooter className="pt-0">
        <SidebarSeparator />
        <SidebarProfileNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

